"""
Telegram Bot Integration Service.
Provides seamless two-way AI sales interactions over Telegram.
Supports RAG knowledge retrieval, live inventory queries, lead qualification, and human takeover.
"""
import asyncio
import logging
from typing import Any, Dict, Optional
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.customer import Customer
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.services.ai_sales_engine import AISalesEngine

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._offset = 0

    @property
    def bot_token(self) -> str:
        raw = settings.TELEGRAM_BOT_TOKEN or ""
        return raw.strip('"\'')

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    async def send_message(self, chat_id: int | str, text: str) -> bool:
        """Send a text message to a Telegram chat."""
        if not self.bot_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
                return resp.status_code == 200
        except Exception as exc:
            logger.error("Failed to send Telegram message to chat %s: %s", chat_id, exc)
            return False

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        """Send a chat action indicator (e.g. typing) to Telegram."""
        if not self.bot_token:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    f"{self.base_url}/sendChatAction",
                    json={"chat_id": chat_id, "action": action},
                )
        except Exception:
            pass

    async def start_polling(self) -> None:
        """Start long-polling Telegram for incoming messages."""
        if not settings.TELEGRAM_BOT_ENABLED or not self.bot_token:
            logger.info("Telegram Bot integration is disabled or token is missing.")
            return

        if self._running:
            logger.warning("Telegram polling is already running.")
            return

        self._running = True
        logger.info("Starting Telegram Bot listener polling...")

        while self._running:
            try:
                async with httpx.AsyncClient(timeout=35) as client:
                    resp = await client.get(
                        f"{self.base_url}/getUpdates",
                        params={"offset": self._offset, "timeout": 25},
                    )

                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])
                        for update in updates:
                            self._offset = max(self._offset, update["update_id"] + 1)
                            # Handle update concurrently so polling continues uninterrupted
                            asyncio.create_task(self.handle_update(update))
                    elif resp.status_code == 409:
                        logger.warning("Telegram conflict (multiple pollers active). Retrying in 5s...")
                        await asyncio.sleep(5)
                    else:
                        logger.warning("Telegram getUpdates returned HTTP %s. Retrying in 3s...", resp.status_code)
                        await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self._running:
                    logger.error("Error in Telegram polling loop: %s. Retrying in 4s...", exc)
                    await asyncio.sleep(4)

        logger.info("Telegram Bot listener stopped.")

    async def stop_polling(self) -> None:
        """Gracefully stop polling."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def handle_update(self, update: Dict[str, Any], db: Optional[AsyncSession] = None) -> None:
        """Process an individual Telegram update."""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        # Ignore messages from bots
        from_user = message.get("from", {})
        if from_user.get("is_bot", False):
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        if not chat_id or not text:
            return

        # Friendly greeting for /start command
        if text.startswith("/start"):
            welcome_msg = (
                "👋 Hello! Welcome to our AI Sales & Support Assistant.\n\n"
                "I can help you explore our product catalog, check real-time stock availability, "
                "and answer questions about pricing, features, and enterprise offerings.\n\n"
                "What can I help you with today?"
            )
            await self.send_message(chat_id, welcome_msg)
            return

        # Send typing action to Telegram immediately
        await self.send_chat_action(chat_id, "typing")

        if db is not None:
            await self._process_message_with_db(db, chat_id, text, from_user)
        else:
            async with AsyncSessionLocal() as session:
                await self._process_message_with_db(session, chat_id, text, from_user)

    async def _process_message_with_db(
        self,
        db: AsyncSession,
        chat_id: int | str,
        text: str,
        from_user: Dict[str, Any],
    ) -> None:
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        username = from_user.get("username", "")
        customer_name = f"{first_name} {last_name}".strip() or username or f"Telegram User {chat_id}"
        phone_key = f"+tg_{chat_id}"

        try:
            # 1. Resolve Active Tenant
            stmt_tenant = (
                select(Tenant)
                .where(Tenant.status == "ACTIVE")
                .order_by(Tenant.created_at.asc())
                .limit(1)
            )
            tenant = (await db.execute(stmt_tenant)).scalar_one_or_none()
            if not tenant:
                logger.warning("No active tenant found to handle Telegram conversation.")
                await self.send_message(
                    chat_id,
                    "Thank you for contacting us! Our team is currently configuring the system. Please check back shortly.",
                )
                return

            # 2. Find or Create Customer
            stmt_cust = select(Customer).where(
                Customer.tenant_id == tenant.id,
                Customer.phone == phone_key,
            )
            customer = (await db.execute(stmt_cust)).scalar_one_or_none()
            if not customer:
                customer = Customer(
                    tenant_id=tenant.id,
                    name=customer_name,
                    phone=phone_key,
                    company_name=f"@{username}" if username else None,
                )
                db.add(customer)
                await db.commit()
                await db.refresh(customer)
            elif not customer.name and customer_name:
                customer.name = customer_name
                await db.commit()

            # 3. Find or Create Conversation
            stmt_conv = (
                select(Conversation)
                .where(
                    Conversation.tenant_id == tenant.id,
                    Conversation.customer_id == customer.id,
                    Conversation.channel == "TELEGRAM",
                    Conversation.status != "RESOLVED",
                )
                .order_by(Conversation.created_at.desc())
                .limit(1)
            )
            conv = (await db.execute(stmt_conv)).scalar_one_or_none()
            if not conv:
                conv = Conversation(
                    tenant_id=tenant.id,
                    customer_id=customer.id,
                    channel="TELEGRAM",
                    status="ACTIVE_AI",
                )
                db.add(conv)
                await db.commit()
                await db.refresh(conv)

            # 4. Handle Human Takeover State
            if conv.status == "ESCALATED_HUMAN":
                cust_msg = Message(
                    conversation_id=conv.id,
                    sender_type="CUSTOMER",
                    content=text,
                )
                db.add(cust_msg)
                conv.last_activity_at = cust_msg.created_at
                await db.commit()
                return

            # 5. Process turn with AISalesEngine (RAG docs, warehouse inventory, lead scoring, round-robin)
            result = await AISalesEngine.process_customer_message(
                db=db,
                tenant_id=tenant.id,
                customer_id=customer.id,
                conversation_id=conv.id,
                user_message_text=text,
            )

            # 6. Send AI reply back to Telegram chat
            reply_text = result.get("reply", "")
            if reply_text:
                await self.send_message(chat_id, reply_text)

        except Exception as exc:
            logger.error("Error processing Telegram message from %s: %s", chat_id, exc, exc_info=True)
            await self.send_message(
                chat_id,
                "Thank you for your message! Our sales advisors are reviewing your inquiry and will follow up shortly.",
            )


# Global singleton instance
telegram_service = TelegramService()
