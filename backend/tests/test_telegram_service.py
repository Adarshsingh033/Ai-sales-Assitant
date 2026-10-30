"""
Tests for Telegram Bot Integration Service.
Verifies update parsing, customer/conversation auto-creation, AI sales reply, and omnichannel dispatch.
"""
import uuid
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy import select

from app.models.customer import Customer
from app.models.conversation import Conversation, Message
from app.models.tenant import Tenant
from app.services.telegram_service import TelegramService


@pytest.mark.asyncio
async def test_telegram_handle_start_command(test_db):
    service = TelegramService()

    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        update = {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {"id": 12345, "first_name": "Alice", "is_bot": False},
                "chat": {"id": 12345},
                "text": "/start",
            },
        }

        await service.handle_update(update)

        mock_send.assert_awaited_once()
        args, kwargs = mock_send.await_args
        assert args[0] == 12345
        assert "Welcome to our AI Sales" in args[1]


@pytest.mark.asyncio
async def test_telegram_sales_inquiry_flow(test_db):
    # Ensure active tenant exists
    stmt = select(Tenant).where(Tenant.status == "ACTIVE").limit(1)
    tenant = (await test_db.execute(stmt)).scalar_one_or_none()
    if not tenant:
        tenant = Tenant(name="Acme Corp", slug=f"acme-{uuid.uuid4().hex[:6]}", status="ACTIVE")
        test_db.add(tenant)
        await test_db.commit()
        await test_db.refresh(tenant)

    service = TelegramService()

    chat_id = 987654321
    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send, \
         patch.object(service, "send_chat_action", new_callable=AsyncMock) as mock_typing, \
         patch("app.services.ai_sales_engine.GroqClient.chat_completion", new_callable=AsyncMock) as mock_groq:

        mock_groq.return_value = "Yes! We have Enterprise CRM in stock and available for immediate deployment."
        mock_send.return_value = True

        update = {
            "update_id": 1002,
            "message": {
                "message_id": 2,
                "from": {
                    "id": chat_id,
                    "first_name": "Bob",
                    "last_name": "Smith",
                    "username": "bobsmith",
                    "is_bot": False,
                },
                "chat": {"id": chat_id},
                "text": "Hi, do you have any CRM licenses available for a team of 50?",
            },
        }

        await service.handle_update(update, db=test_db)

        # 1. Typing action sent
        mock_typing.assert_awaited_once_with(chat_id, "typing")

        # 2. Reply sent to customer
        mock_send.assert_awaited_once()
        args, _ = mock_send.await_args
        assert args[0] == chat_id
        assert "Enterprise CRM" in args[1]

        # 3. Verify Customer was created in DB
        stmt_cust = select(Customer).where(Customer.phone == f"+tg_{chat_id}")
        cust = (await test_db.execute(stmt_cust)).scalar_one_or_none()
        assert cust is not None
        assert cust.name == "Bob Smith"

        # 4. Verify Conversation was created in DB with TELEGRAM channel
        stmt_conv = select(Conversation).where(
            Conversation.customer_id == cust.id,
            Conversation.channel == "TELEGRAM",
        )
        conv = (await test_db.execute(stmt_conv)).scalar_one_or_none()
        assert conv is not None
        assert conv.status in ("ACTIVE_AI", "WAITING_SALES", "ESCALATED_HUMAN")
