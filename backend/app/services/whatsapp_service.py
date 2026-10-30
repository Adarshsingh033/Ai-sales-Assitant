"""
Meta WhatsApp Cloud API Service.

Inbound  verify the webhook signature, resolve the tenant STRICTLY by phone_number_id, de-duplicate by message id,
         enqueue one job per message and acknowledge immediately (Meta retries slow webhooks).
Outbound free-text messages (inside the 24h window) and approved templates (outside it).
"""
import hashlib
import hmac
import logging
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.ai_assistant_config import AIAssistantConfig
from app.services.channels import InboundProcessor
from app.services.jobs import JobQueue, lane_key

logger = logging.getLogger(__name__)

META_GRAPH_URL = "https://graph.facebook.com/v21.0"


def verify_signature(raw_body: bytes, header: Optional[str], app_secret: Optional[str]) -> bool:
    """Validate Meta's X-Hub-Signature-256 header (`sha256=<hex hmac of the raw body>`)."""
    if not header or not app_secret:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def extract_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten every entry/change/message of a webhook payload into simple dicts (text and button replies)."""
    out: List[Dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []
            }
            for msg in value.get("messages") or []:
                kind = msg.get("type")
                if kind == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif kind == "interactive":
                    inter = msg.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    text = reply.get("title", "")
                elif kind == "button":
                    text = (msg.get("button") or {}).get("text", "")
                else:
                    continue  # media/status events are not handled yet
                sender = msg.get("from")
                if sender and text.strip():
                    out.append(
                        {
                            "phone_number_id": phone_number_id,
                            "message_id": msg.get("id"),
                            "from": sender,
                            "text": text.strip(),
                            "name": names.get(sender),
                        }
                    )
    return out


class WhatsAppService:
    @staticmethod
    async def _post(phone_number_id: str, access_token: str, body: Dict[str, Any]) -> bool:
        from app.services.ai.llm import get_http_client

        url = f"{META_GRAPH_URL}/{phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        try:
            res = await get_http_client().post(url, headers=headers, json=body, timeout=15.0)
            if res.status_code not in (200, 201):
                logger.error("WhatsApp delivery failed (%s): %s", res.status_code, res.text[:300])
                return False
            return True
        except Exception as exc:
            logger.error("Error sending WhatsApp message: %s", exc)
            return False

    @classmethod
    async def send_whatsapp_message(
        cls, phone_number_id: str, access_token: str, to_phone: str, message_text: str
    ) -> bool:
        """Send a free-form text message (only valid inside the 24h customer-service window)."""
        if not phone_number_id or not access_token:
            logger.warning("WhatsApp credentials missing; skipping outbound delivery.")
            return False
        return await cls._post(
            phone_number_id,
            access_token,
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_phone,
                "type": "text",
                "text": {"body": message_text},
            },
        )

    @classmethod
    async def send_whatsapp_template(
        cls,
        phone_number_id: str,
        access_token: str,
        to_phone: str,
        template_name: str,
        params: List[str],
        language: str = "en",
    ) -> bool:
        """Send an approved template (the only message type allowed outside the 24h window)."""
        components = (
            [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else []
        )
        return await cls._post(
            phone_number_id,
            access_token,
            {
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "template",
                "template": {"name": template_name, "language": {"code": language}, "components": components},
            },
        )

    @staticmethod
    async def find_config(db: AsyncSession, phone_number_id: Optional[str]) -> Optional[AIAssistantConfig]:
        """Strict tenant resolution — an unknown phone_number_id is never routed to some other tenant."""
        if not phone_number_id:
            return None
        return (
            await db.execute(
                select(AIAssistantConfig).where(AIAssistantConfig.whatsapp_phone_number_id == phone_number_id)
            )
        ).scalars().first()

    @staticmethod
    def app_secret_for(config: Optional[AIAssistantConfig]) -> Optional[str]:
        return (decrypt_secret(config.whatsapp_app_secret) if config else None) or settings.WHATSAPP_APP_SECRET or None

    @classmethod
    async def process_inbound_webhook(cls, db: AsyncSession, payload: Dict[str, Any]) -> Dict[str, int]:
        """Queue every new text message in the payload. Returns counters (queued / duplicates / ignored)."""
        counters = {"queued": 0, "duplicates": 0, "ignored": 0}
        for msg in extract_messages(payload):
            config = await cls.find_config(db, msg["phone_number_id"])
            if config is None:
                logger.warning("WhatsApp message for unknown phone_number_id %s ignored", msg["phone_number_id"])
                counters["ignored"] += 1
                continue
            if msg["message_id"] and not await InboundProcessor.claim_event(
                db, "WHATSAPP", msg["message_id"], config.tenant_id
            ):
                counters["duplicates"] += 1
                continue
            await JobQueue.enqueue(
                db,
                "INBOUND_MESSAGE",
                {
                    "channel": "WHATSAPP",
                    "phone": msg["from"],
                    "text": msg["text"],
                    "customer_name": msg["name"] or f"WhatsApp User ({msg['from'][-4:]})",
                },
                tenant_id=config.tenant_id,
                conversation_id=lane_key(config.tenant_id, msg["from"]),
                commit=False,
            )
            await db.commit()
            counters["queued"] += 1
        return counters
