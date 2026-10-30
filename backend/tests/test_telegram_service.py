"""
Tests for the shared-bot Telegram integration: deep-link tenant binding, strict routing, idempotency,
AI reply delivery and customer/conversation creation.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.channel import ChannelBinding
from app.models.conversation import Conversation
from app.models.customer import Customer
from app.models.tenant import Tenant
from app.services.telegram_service import UNBOUND_REPLY, TelegramService


def _msg(update_id: int, chat_id: int, text: str, first_name: str = "Bob", **extra):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": chat_id, "first_name": first_name, "is_bot": False, **extra},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


async def _tenant(test_db, name="Acme Corp"):
    t = Tenant(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}", status="ACTIVE")
    test_db.add(t)
    await test_db.commit()
    await test_db.refresh(t)
    return t


@pytest.mark.asyncio
async def test_bare_start_command_sends_generic_welcome():
    service = TelegramService()
    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True
        await service.handle_update(_msg(1001, 12345, "/start", "Alice"))
        mock_send.assert_awaited_once()
        args, _ = mock_send.await_args
        assert args[0] == 12345
        assert "Welcome to our AI Sales" in args[1]


@pytest.mark.asyncio
async def test_start_deep_link_pins_chat_to_tenant(test_db):
    tenant = await _tenant(test_db, "Deep Link Co")
    service = TelegramService()
    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send:
        await service.handle_update(_msg(1002, 555001, f"/start {tenant.slug}"), db=test_db)
        assert "Welcome to Deep Link Co" in mock_send.await_args.args[1]

    binding = (
        await test_db.execute(select(ChannelBinding).where(ChannelBinding.external_id == "555001"))
    ).scalar_one()
    assert binding.tenant_id == tenant.id

    # invalid code → no binding change, friendly error
    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send:
        await service.handle_update(_msg(1003, 555002, "/start no-such-company"), db=test_db)
        assert "not valid" in mock_send.await_args.args[1]


@pytest.mark.asyncio
async def test_sales_inquiry_flow_routes_to_bound_tenant(test_db, fake_llm):
    tenant = await _tenant(test_db)
    await _tenant(test_db, "Other Co")  # a second active tenant must NOT receive this chat
    fake_llm.replies = ["Yes! We have Enterprise CRM in stock and available for immediate deployment."]
    fake_llm.analysis = {"intent": "product_inquiry", "buying_intent": True, "quantity": 50}

    service = TelegramService()
    chat_id = 987654321
    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send, \
         patch.object(service, "send_chat_action", new_callable=AsyncMock) as mock_typing:
        mock_send.return_value = True
        await service.handle_update(_msg(2001, chat_id, f"/start {tenant.slug}"), db=test_db)
        mock_send.reset_mock()

        await service.handle_update(
            _msg(2002, chat_id, "Hi, do you have any CRM licenses available for a team of 50?",
                 "Bob", last_name="Smith", username="bobsmith"),
            db=test_db,
        )

        mock_typing.assert_awaited_once_with(chat_id, "typing")
        mock_send.assert_awaited_once()
        assert mock_send.await_args.args[0] == chat_id
        assert "Enterprise CRM" in mock_send.await_args.args[1]

    cust = (await test_db.execute(select(Customer).where(Customer.phone == f"+tg_{chat_id}"))).scalar_one()
    assert cust.tenant_id == tenant.id
    assert cust.name == "Bob Smith"
    conv = (
        await test_db.execute(select(Conversation).where(Conversation.customer_id == cust.id, Conversation.channel == "TELEGRAM"))
    ).scalar_one()
    assert conv.tenant_id == tenant.id
    assert conv.status in ("ACTIVE_AI", "WAITING_SALES", "ESCALATED_HUMAN")


@pytest.mark.asyncio
async def test_unbound_chat_is_never_routed_to_an_arbitrary_tenant(test_db, fake_llm):
    await _tenant(test_db, "First Co")
    await _tenant(test_db, "Second Co")  # ≥2 active tenants → ambiguity
    service = TelegramService()
    chat_id = 777000111
    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send, \
         patch.object(service, "send_chat_action", new_callable=AsyncMock):
        await service.handle_update(_msg(3001, chat_id, "Do you sell CRMs?"), db=test_db)
        mock_send.assert_awaited_once()
        assert mock_send.await_args.args[1] == UNBOUND_REPLY

    assert not fake_llm.reply_calls()
    assert (await test_db.execute(select(Customer).where(Customer.phone == f"+tg_{chat_id}"))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_redelivered_update_is_processed_once(test_db, fake_llm):
    tenant = await _tenant(test_db)
    fake_llm.replies = ["First answer.", "Second answer (must not happen)."]
    service = TelegramService()
    chat_id = 424242
    with patch.object(service, "send_message", new_callable=AsyncMock) as mock_send, \
         patch.object(service, "send_chat_action", new_callable=AsyncMock):
        await service.handle_update(_msg(4001, chat_id, f"/start {tenant.slug}"), db=test_db)
        mock_send.reset_mock()
        update = _msg(4002, chat_id, "How much is the enterprise plan?")
        await service.handle_update(update, db=test_db)
        await service.handle_update(update, db=test_db)  # Telegram re-delivers after a restart
        assert mock_send.await_count == 1
    assert len(fake_llm.reply_calls()) == 1
