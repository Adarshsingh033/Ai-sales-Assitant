"""
Telegram Bot Integration Service — one shared bot, many companies.

Each chat is pinned to one tenant through the deep link `https://t.me/<bot>?start=<tenant-slug>` (stored in
`channel_bindings`). If exactly one tenant is ACTIVE on the platform an unbound chat is pinned to it automatically
(single-company deployments keep working); otherwise the user is asked to open their company's link — messages are
never routed to an arbitrary tenant. Updates are de-duplicated by update_id.
"""
import asyncio
import logging
from typing import Any, Dict, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.channel import ChannelBinding
from app.models.tenant import Tenant, TenantStatus
from app.services.ai.llm import get_http_client
from app.services.channels import InboundProcessor

logger = logging.getLogger(__name__)

GENERIC_WELCOME = (
    "👋 Hello! Welcome to our AI Sales & Support Assistant.\n\n"
    "I can help you explore our product catalog, check real-time stock availability, "
    "and answer questions about pricing, features, and enterprise offerings.\n\n"
    "What can I help you with today?"
)
UNBOUND_REPLY = (
    "Hi! To reach the right company, please open the chat link your company shared with you "
    "(it looks like t.me/<bot>?start=<company-code>)."
)
ERROR_REPLY = "Thank you for your message! Our sales advisors are reviewing your inquiry and will follow up shortly."


class TelegramService:
    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._offset = 0
        self._inflight: Set[asyncio.Task] = set()
        self.bot_username: Optional[str] = None

    # -- config ---------------------------------------------------------
    @property
    def bot_token(self) -> str:
        return (settings.TELEGRAM_BOT_TOKEN or "").strip("\"'")

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}"

    def start_link(self, tenant_slug: str) -> Optional[str]:
        return f"https://t.me/{self.bot_username}?start={tenant_slug}" if self.bot_username else None

    # -- Bot API --------------------------------------------------------
    async def send_message(self, chat_id: int | str, text: str) -> bool:
        if not self.bot_token:
            return False
        try:
            resp = await get_http_client().post(
                f"{self.base_url}/sendMessage", json={"chat_id": chat_id, "text": text[:4096]}, timeout=10
            )
            if resp.status_code != 200:
                logger.warning("Telegram sendMessage failed (%s): %s", resp.status_code, resp.text[:200])
            return resp.status_code == 200
        except Exception as exc:
            logger.error("Failed to send Telegram message to chat %s: %s", chat_id, exc)
            return False

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        if not self.bot_token:
            return
        try:
            await get_http_client().post(
                f"{self.base_url}/sendChatAction", json={"chat_id": chat_id, "action": action}, timeout=5
            )
        except Exception:
            pass

    async def _load_bot_username(self) -> None:
        try:
            resp = await get_http_client().get(f"{self.base_url}/getMe", timeout=10)
            if resp.status_code == 200:
                self.bot_username = (resp.json().get("result") or {}).get("username")
        except Exception as exc:
            logger.warning("Could not read Telegram bot username: %s", exc)

    # -- polling --------------------------------------------------------
    def launch(self) -> None:
        """Start polling as a background task (called at application startup)."""
        self._task = asyncio.create_task(self.start_polling())

    async def start_polling(self) -> None:
        if not settings.TELEGRAM_BOT_ENABLED or not self.bot_token:
            logger.info("Telegram Bot integration is disabled or token is missing.")
            return
        if self._running:
            logger.warning("Telegram polling is already running.")
            return

        self._running = True
        await self._load_bot_username()
        logger.info("Starting Telegram Bot listener polling (bot=@%s)...", self.bot_username)

        while self._running:
            try:
                resp = await get_http_client().get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": self._offset, "timeout": 25, "allowed_updates": '["message","edited_message"]'},
                    timeout=35,
                )
                if resp.status_code == 200:
                    for update in resp.json().get("result", []):
                        self._offset = max(self._offset, update["update_id"] + 1)
                        task = asyncio.create_task(self.handle_update(update))
                        self._inflight.add(task)  # keep a reference so tasks are not garbage-collected mid-flight
                        task.add_done_callback(self._inflight.discard)
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
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- update handling ------------------------------------------------
    async def handle_update(self, update: Dict[str, Any], db: Optional[AsyncSession] = None) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        from_user = message.get("from", {})
        if from_user.get("is_bot", False):
            return
        chat_id = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        if text.startswith("/start"):
            payload = text[len("/start"):].strip()
            if not payload:
                await self.send_message(chat_id, GENERIC_WELCOME)
                return
            await self._with_db(db, self._handle_start_link, chat_id, payload)
            return

        await self.send_chat_action(chat_id, "typing")
        await self._with_db(db, self._process_message_with_db, chat_id, text, from_user, update.get("update_id"))

    @staticmethod
    async def _with_db(db: Optional[AsyncSession], fn, *args) -> None:
        if db is not None:
            await fn(db, *args)
            return
        async with AsyncSessionLocal() as session:
            await fn(session, *args)

    async def _handle_start_link(self, db: AsyncSession, chat_id: int | str, slug: str) -> None:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == slug, Tenant.status == TenantStatus.ACTIVE))
        ).scalar_one_or_none()
        if tenant is None:
            await self.send_message(chat_id, "Sorry, that company link is not valid. Please check the link you were given.")
            return
        await self._bind(db, chat_id, tenant)
        cfg = (
            await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant.id))
        ).scalar_one_or_none()
        greeting = (cfg.greeting_message if cfg else None) or "How can I help you today?"
        await self.send_message(chat_id, f"👋 Welcome to {tenant.name}!\n\n{greeting}")

    @staticmethod
    async def _bind(db: AsyncSession, chat_id: int | str, tenant: Tenant) -> None:
        binding = (
            await db.execute(
                select(ChannelBinding).where(ChannelBinding.channel == "TELEGRAM", ChannelBinding.external_id == str(chat_id))
            )
        ).scalar_one_or_none()
        if binding is None:
            db.add(ChannelBinding(channel="TELEGRAM", external_id=str(chat_id), tenant_id=tenant.id))
        else:
            binding.tenant_id = tenant.id
        await db.commit()

    async def _resolve_tenant(self, db: AsyncSession, chat_id: int | str) -> Optional[Tenant]:
        binding = (
            await db.execute(
                select(ChannelBinding).where(ChannelBinding.channel == "TELEGRAM", ChannelBinding.external_id == str(chat_id))
            )
        ).scalar_one_or_none()
        if binding:
            tenant = (
                await db.execute(select(Tenant).where(Tenant.id == binding.tenant_id, Tenant.status == TenantStatus.ACTIVE))
            ).scalar_one_or_none()
            if tenant:
                return tenant
        active = (
            await db.execute(select(Tenant).where(Tenant.status == TenantStatus.ACTIVE).order_by(Tenant.created_at).limit(2))
        ).scalars().all()
        if len(active) == 1:  # single-company deployment: pin automatically
            await self._bind(db, chat_id, active[0])
            return active[0]
        return None

    async def _process_message_with_db(
        self,
        db: AsyncSession,
        chat_id: int | str,
        text: str,
        from_user: Dict[str, Any],
        update_id: Optional[int] = None,
    ) -> None:
        first_name = from_user.get("first_name", "")
        last_name = from_user.get("last_name", "")
        username = from_user.get("username", "")
        customer_name = f"{first_name} {last_name}".strip() or username or f"Telegram User {chat_id}"

        try:
            tenant = await self._resolve_tenant(db, chat_id)
            if tenant is None:
                await self.send_message(chat_id, UNBOUND_REPLY)
                return

            if update_id is not None and not await InboundProcessor.claim_event(
                db, "TELEGRAM", f"{chat_id}:{update_id}", tenant.id
            ):
                return  # already handled (e.g. re-delivered after a restart)
            await db.commit()

            await InboundProcessor.process(
                db,
                tenant_id=tenant.id,
                channel="TELEGRAM",
                phone=f"+tg_{chat_id}",
                text=text,
                customer_name=customer_name,
                company_hint=f"@{username}" if username else None,
                deliver_fn=lambda reply: self.send_message(chat_id, reply),  # sent BEFORE post-reply analysis
            )
        except Exception as exc:
            logger.error("Error processing Telegram message from %s: %s", chat_id, exc, exc_info=True)
            await db.rollback()
            await self.send_message(chat_id, ERROR_REPLY)


# Global singleton instance
telegram_service = TelegramService()
