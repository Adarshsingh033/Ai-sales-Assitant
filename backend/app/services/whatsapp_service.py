"""
Meta WhatsApp Cloud API Service.
Handles inbound webhook messages, customer identification, AI sales dispatch,
and outbound message delivery via official Meta WhatsApp Graph API.
"""
import logging
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.services.ai_sales_engine import AISalesEngine

logger = logging.getLogger(__name__)

META_GRAPH_URL = "https://graph.facebook.com/v21.0"


class WhatsAppService:
    @staticmethod
    async def send_whatsapp_message(
        phone_number_id: str,
        access_token: str,
        to_phone: str,
        message_text: str,
    ) -> bool:
        """
        Send outbound text message via Meta WhatsApp Cloud API.
        """
        if not phone_number_id or not access_token:
            logger.warning("WhatsApp credentials missing; skipping outbound delivery.")
            return False

        url = f"{META_GRAPH_URL}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"body": message_text},
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, headers=headers, json=payload)
            if res.status_code not in (200, 201):
                logger.error("WhatsApp delivery failed (%s): %s", res.status_code, res.text)
                return False
            return True
        except Exception as exc:
            logger.error("Error sending WhatsApp message: %s", exc)
            return False

    @classmethod
    async def process_inbound_webhook(
        cls,
        db: AsyncSession,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Parse Meta WhatsApp Cloud API webhook, invoke AISalesEngine, and reply back.
        """
        try:
            entry = payload.get("entry", [])[0]
            change = entry.get("changes", [])[0].get("value", {})
            metadata = change.get("metadata", {})
            phone_number_id = metadata.get("phone_number_id")

            messages = change.get("messages", [])
            if not messages:
                return None  # Status updates, delivery receipts

            msg_obj = messages[0]
            if msg_obj.get("type") != "text":
                return None  # For MVP, focus on text messages

            from_phone = msg_obj.get("from")
            text_body = msg_obj.get("text", {}).get("body", "").strip()
            if not from_phone or not text_body:
                return None

            contacts = change.get("contacts", [])
            contact_name = contacts[0].get("profile", {}).get("name") if contacts else None

            # Find matching tenant by whatsapp_phone_number_id or default to first configured tenant
            stmt_cfg = select(AIAssistantConfig).where(
                AIAssistantConfig.whatsapp_phone_number_id == phone_number_id
            )
            config = (await db.execute(stmt_cfg)).scalar_one_or_none()
            if not config:
                # Fallback to any enabled config
                stmt_any = select(AIAssistantConfig).limit(1)
                config = (await db.execute(stmt_any)).scalar_one_or_none()
                if not config:
                    logger.error("No tenant AI assistant found for inbound WhatsApp message.")
                    return None

            tenant_id = config.tenant_id

            # Find or create customer
            stmt_cust = select(Customer).where(
                Customer.tenant_id == tenant_id,
                Customer.phone == from_phone,
            )
            customer = (await db.execute(stmt_cust)).scalar_one_or_none()
            if not customer:
                customer = Customer(
                    tenant_id=tenant_id,
                    phone=from_phone,
                    name=contact_name or f"WhatsApp User ({from_phone[-4:]})",
                )
                db.add(customer)
                await db.flush()

            # Find or create active conversation
            stmt_conv = select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.customer_id == customer.id,
                Conversation.channel == "WHATSAPP",
                Conversation.status.in_(["ACTIVE_AI", "WAITING_SALES", "ESCALATED_HUMAN"]),
            ).order_by(Conversation.created_at.desc())
            conversation = (await db.execute(stmt_conv)).scalars().first()

            if not conversation:
                conversation = Conversation(
                    tenant_id=tenant_id,
                    customer_id=customer.id,
                    channel="WHATSAPP",
                    status="ACTIVE_AI",
                )
                db.add(conversation)
                await db.flush()

            # If human sales rep has taken over, don't generate AI reply
            if conversation.status == "ESCALATED_HUMAN":
                # Just save customer message
                from app.models.conversation import Message
                db.add(Message(conversation_id=conversation.id, sender_type="CUSTOMER", content=text_body))
                conversation.last_activity_at = config.updated_at
                await db.commit()
                return {"status": "saved_for_human"}

            # Process with AI Sales Engine
            result = await AISalesEngine.process_customer_message(
                db=db,
                tenant_id=tenant_id,
                customer_id=customer.id,
                conversation_id=conversation.id,
                user_message_text=text_body,
            )

            # Send reply back via WhatsApp Cloud API
            access_token = config.whatsapp_access_token or settings.WHATSAPP_ACCESS_TOKEN
            active_phone_id = config.whatsapp_phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID

            if access_token and active_phone_id:
                await cls.send_whatsapp_message(
                    phone_number_id=active_phone_id,
                    access_token=access_token,
                    to_phone=from_phone,
                    message_text=result["reply"],
                )

            return result

        except Exception as exc:
            logger.error("Failed to process WhatsApp webhook payload: %s", exc)
            return None
