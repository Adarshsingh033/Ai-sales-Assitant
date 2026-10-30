"""
Channel plumbing shared by WhatsApp, Telegram and the web simulator:

  * ChannelSender   — one `send()` for every outbound path (AI replies, follow-ups, human replies).
  * InboundProcessor — resolve customer + conversation, honour human takeover, run the AI turn, deliver the reply.
"""
import asyncio
import logging
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.channel import InboundEvent
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.services.ai.orchestrator import ConversationOrchestrator

logger = logging.getLogger(__name__)

WHATSAPP_WINDOW = timedelta(hours=24)
HUMAN_STATES = ("ESCALATED_HUMAN", "HUMAN_AGENT")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def whatsapp_credentials(config: Optional[AIAssistantConfig]) -> tuple[Optional[str], Optional[str]]:
    """(phone_number_id, access_token) for a tenant, decrypted, falling back to platform-level env values."""
    phone_id = (config.whatsapp_phone_number_id if config else None) or settings.WHATSAPP_PHONE_NUMBER_ID
    token = (decrypt_secret(config.whatsapp_access_token) if config else None) or settings.WHATSAPP_ACCESS_TOKEN
    return phone_id or None, token or None


async def whatsapp_window_open(db: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """WhatsApp only allows free-form messages within 24h of the customer's last message."""
    last = (
        await db.execute(
            select(Message.created_at)
            .where(Message.conversation_id == conversation_id, Message.sender_type == "CUSTOMER")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return _utcnow() - last < WHATSAPP_WINDOW


class ChannelSender:
    @staticmethod
    async def send(
        db: AsyncSession,
        conversation: Conversation,
        customer: Customer,
        config: Optional[AIAssistantConfig],
        text: str,
    ) -> bool:
        """Deliver text on the conversation's channel. Returns True when it reached (or needs no) external delivery."""
        channel = conversation.channel
        if channel == "WEB_SIMULATOR":
            return True
        if not text:
            return False
        if channel == "WHATSAPP":
            from app.services.whatsapp_service import WhatsAppService

            phone_id, token = whatsapp_credentials(config)
            if not (phone_id and token and customer.phone):
                logger.warning("WhatsApp credentials/phone missing; reply not delivered (conv %s)", conversation.id)
                return False
            return await WhatsAppService.send_whatsapp_message(phone_id, token, customer.phone, text)
        if channel == "TELEGRAM":
            from app.services.telegram_service import telegram_service

            chat_id = (customer.phone or "").replace("+tg_", "").replace("tg_", "")
            return await telegram_service.send_message(chat_id, text) if chat_id else False
        logger.warning("Unknown channel %s; message not delivered", channel)
        return False


class InboundProcessor:
    # Weak values: a lock disappears as soon as no turn holds or awaits it, so the map cannot grow unbounded.
    _locks: "weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock]" = weakref.WeakValueDictionary()

    @classmethod
    async def claim_event(cls, db: AsyncSession, channel: str, external_id: str, tenant_id: uuid.UUID) -> bool:
        """Idempotency: True the first time a provider message id is seen, False for retries/duplicates."""
        try:
            async with db.begin_nested():
                db.add(InboundEvent(channel=channel, external_id=external_id, tenant_id=tenant_id))
                await db.flush()
            return True
        except IntegrityError:
            return False

    @classmethod
    async def process(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        channel: str,
        phone: str,
        text: str,
        customer_name: Optional[str] = None,
        company_hint: Optional[str] = None,
        analysis_inline: bool = False,
        deliver: bool = True,
        deliver_fn: Optional[Callable[[str], Awaitable[bool]]] = None,
    ) -> Optional[Dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return None

        customer = (
            await db.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone == phone))
        ).scalar_one_or_none()
        if customer is None:
            customer = Customer(tenant_id=tenant_id, phone=phone, name=customer_name, company_name=company_hint)
            db.add(customer)
            await db.flush()
        elif not customer.name and customer_name:
            customer.name = customer_name

        conversation = (
            await db.execute(
                select(Conversation)
                .where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.customer_id == customer.id,
                    Conversation.channel == channel,
                    Conversation.status != "RESOLVED",
                )
                .order_by(Conversation.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if conversation is None:
            conversation = Conversation(tenant_id=tenant_id, customer_id=customer.id, channel=channel, status="ACTIVE_AI")
            db.add(conversation)
            await db.flush()
        await db.commit()

        lock = cls._locks.get(conversation.id)
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[conversation.id] = lock
        async with lock:  # keep one customer's turns strictly ordered within this process
            await db.refresh(conversation)
            if conversation.status in HUMAN_STATES:
                db.add(Message(conversation_id=conversation.id, sender_type="CUSTOMER", content=text))
                conversation.last_activity_at = _utcnow()
                await db.commit()
                return {"status": "saved_for_human", "conversation_id": str(conversation.id)}

            turn = await ConversationOrchestrator.handle_message(
                db,
                tenant_id=tenant_id,
                customer_id=customer.id,
                conversation_id=conversation.id,
                user_message_text=text,
                trigger="INBOUND",
                analysis_inline=analysis_inline,
            )
            result = turn.as_dict()
            if deliver and turn.reply:
                if deliver_fn is not None:
                    result["delivered"] = await deliver_fn(turn.reply)
                else:
                    config = (
                        await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant_id))
                    ).scalar_one_or_none()
                    result["delivered"] = await ChannelSender.send(db, conversation, customer, config, turn.reply)
            if turn.pending:  # analyse AFTER the customer has their answer
                try:
                    outcome = await ConversationOrchestrator.post_process_turn(db, **turn.pending)
                    result.update(
                        intent=outcome["intent"], score=outcome["score"], score_reason=outcome["score_reason"],
                        lead_id=outcome["lead_id"], needs_human_handoff=outcome["needs_human_handoff"],
                    )
                except Exception:
                    logger.exception("Post-reply analysis failed for conversation %s", conversation.id)
                    await db.rollback()
            result["conversation_id"] = str(conversation.id)
            return result
