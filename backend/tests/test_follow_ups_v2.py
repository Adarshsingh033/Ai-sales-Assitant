"""AI follow-ups: drafting, guardrails, delivery rules per channel, stop conditions, attempt chains, approvals."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.models.ai_usage_log import AIUsageLog
from app.models.conversation import Message
from app.models.follow_up import FollowUpSchedule
from app.models.notification import Notification
from app.services import follow_up_service
from app.services.follow_up_service import FollowUpService, fallback_message
from app.services.telegram_service import telegram_service
from app.services.whatsapp_service import WhatsAppService
from tests.factories import (
    add_message, auth_headers, make_config, make_conversation, make_customer, make_lead, make_tenant,
)

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731
GOOD_DRAFT = "Hi Jordan, just checking in on the CRM rollout for your 20 reps. Happy to set up a quick demo whenever suits!"


async def due(db, *, channel="WEB_SIMULATOR", attempt=1, max_attempts=3, tz=None, last_customer_msg_age=timedelta(hours=30),
              conv_status="ACTIVE_AI", **cfg_kw):
    tenant = await make_tenant(db, timezone=tz)
    cfg = await make_config(db, tenant, auto_followup_enabled=True, followup_delay_hours=24, **cfg_kw)
    customer = await make_customer(db, tenant, name="Jordan Smith", phone="+15551110000")
    conv = await make_conversation(db, tenant, customer, channel=channel, status=conv_status)
    lead = await make_lead(db, tenant, customer, conv, requirements="CRM for 20 reps")
    await add_message(db, conv, "CUSTOMER", "I need a CRM for my 20 reps", created_at=NOW() - last_customer_msg_age)
    await add_message(db, conv, "AI", "Happy to help!", created_at=NOW() - last_customer_msg_age + timedelta(seconds=5))
    sched = FollowUpSchedule(
        tenant_id=tenant.id, lead_id=lead.id, customer_id=customer.id, conversation_id=conv.id,
        scheduled_at=NOW() - timedelta(minutes=1), status="PENDING", attempt_number=attempt, max_attempts=max_attempts, channel=channel,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return SimpleNamespace(tenant=tenant, cfg=cfg, customer=customer, conv=conv, lead=lead, sched=sched)


async def messages(db, conv):
    return [(m.sender_type, m.content) for m in (await db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at))).scalars().all()]


async def all_schedules(db, w):
    return (await db.execute(select(FollowUpSchedule).where(FollowUpSchedule.lead_id == w.lead.id).order_by(FollowUpSchedule.attempt_number))).scalars().all()


async def notifs(db, w, type_="FOLLOWUP_DUE"):
    return (await db.execute(select(Notification).where(Notification.tenant_id == w.tenant.id, Notification.type == type_))).scalars().all()


# ----------------------------------------------------------------------------- drafting
@pytest.mark.asyncio
async def test_ai_drafted_followup_is_sent_and_the_next_attempt_chained(test_db, fake_llm):
    w = await due(test_db)
    fake_llm.replies = [GOOD_DRAFT]
    assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 1

    await test_db.refresh(w.sched)
    assert w.sched.status == "DISPATCHED" and w.sched.custom_message == GOOD_DRAFT and w.sched.executed_at
    assert (await messages(test_db, w.conv))[-1] == ("AI", GOOD_DRAFT)

    prompt = fake_llm.reply_calls()[0]["messages"][0]["content"]
    assert "Attempt 1 of 3" in prompt and "CRM for 20 reps" in prompt and "I need a CRM for my 20 reps" in prompt
    usage = (await test_db.execute(select(AIUsageLog).where(AIUsageLog.tenant_id == w.tenant.id))).scalars().all()
    assert [u.action_type for u in usage] == ["FOLLOWUP_GENERATION"]

    chain = await all_schedules(test_db, w)
    assert [c.attempt_number for c in chain] == [1, 2] and chain[1].status == "PENDING"
    delta = chain[1].scheduled_at.replace(tzinfo=timezone.utc) - NOW()
    assert timedelta(hours=47) < delta < timedelta(hours=49)  # growing delay: 24h × (attempt+1)
    assert len(await notifs(test_db, w)) == 1


@pytest.mark.asyncio
async def test_unsafe_drafts_fall_back_to_the_safe_template(test_db, fake_llm):
    w = await due(test_db)
    fake_llm.replies = ["Great news Jordan: 50% off today only, just $199!"]
    await FollowUpService.process_due_followups(test_db, w.tenant.id)
    await test_db.refresh(w.sched)
    assert w.sched.status == "DISPATCHED"
    assert w.sched.custom_message == fallback_message("Jordan Smith", "CRM for 20 reps", 1)
    assert "$" not in w.sched.custom_message and "%" not in w.sched.custom_message


@pytest.mark.asyncio
async def test_followups_still_go_out_when_the_llm_is_down(test_db):
    w = await due(test_db)  # NullLLM
    assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 1
    await test_db.refresh(w.sched)
    assert w.sched.status == "DISPATCHED" and "Jordan Smith" in w.sched.custom_message


@pytest.mark.asyncio
async def test_the_last_attempt_says_goodbye_and_stops_the_chain(test_db):
    w = await due(test_db, attempt=3, max_attempts=3)
    await FollowUpService.process_due_followups(test_db, w.tenant.id)
    await test_db.refresh(w.sched)
    assert "last note" in w.sched.custom_message
    assert len(await all_schedules(test_db, w)) == 1  # no attempt 4: the AI does not message indefinitely
    assert any("finished" in n.title for n in await notifs(test_db, w))


# ----------------------------------------------------------------------------- stop conditions
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,expected_status,expected_reason", [
    ("customer_replied", "RESPONDED", "CUSTOMER_REPLIED"),
    ("do_not_contact", "CANCELLED", "DO_NOT_CONTACT"),
    ("human_active", "CANCELLED", "HUMAN_ACTIVE"),
    ("resolved", "CANCELLED", "CONVERSATION_RESOLVED"),
    ("lead_won", "CANCELLED", "LEAD_CLOSED"),
    ("followups_disabled", "CANCELLED", "FOLLOWUPS_DISABLED"),
    ("ai_disabled", "CANCELLED", "FOLLOWUPS_DISABLED"),
])
async def test_followups_are_cancelled_when_a_stop_condition_holds(test_db, fake_llm, scenario, expected_status, expected_reason):
    w = await due(test_db)
    if scenario == "customer_replied":
        await add_message(test_db, w.conv, "CUSTOMER", "Sorry, was travelling. Yes, interested!", created_at=NOW() + timedelta(seconds=1))
    elif scenario == "do_not_contact":
        w.customer.do_not_contact = True
    elif scenario == "human_active":
        w.conv.status = "ESCALATED_HUMAN"
    elif scenario == "resolved":
        w.conv.status = "RESOLVED"
    elif scenario == "lead_won":
        w.lead.status = "WON"
    elif scenario == "followups_disabled":
        w.cfg.auto_followup_enabled = False
    elif scenario == "ai_disabled":
        w.cfg.is_enabled = False
    await test_db.commit()
    before = await messages(test_db, w.conv)

    assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 0
    await test_db.refresh(w.sched)
    assert (w.sched.status, w.sched.stop_reason) == (expected_status, expected_reason)
    assert not fake_llm.calls and (await messages(test_db, w.conv))[: len(before)] == before
    assert len(await all_schedules(test_db, w)) == 1


@pytest.mark.asyncio
async def test_scheduling_respects_tenant_policy_and_opt_out(test_db):
    w = await due(test_db, max_followups=2)
    await test_db.execute(FollowUpSchedule.__table__.delete())
    await test_db.commit()

    sched = await FollowUpService.schedule_followup_if_eligible(test_db, w.tenant.id, w.lead.id, w.customer.id, w.conv.id, channel="WEB_SIMULATOR")
    assert sched.max_attempts == 2 and sched.channel == "WEB_SIMULATOR"
    again = await FollowUpService.schedule_followup_if_eligible(test_db, w.tenant.id, w.lead.id, w.customer.id, w.conv.id, delay_hours=72)
    assert again.id == sched.id  # customer active again → timer pushed back, not duplicated

    w.customer.do_not_contact = True
    await test_db.commit()
    await FollowUpService.cancel_for_customer(test_db, w.customer.id, "CUSTOMER_OPTED_OUT")
    assert await FollowUpService.schedule_followup_if_eligible(test_db, w.tenant.id, w.lead.id, w.customer.id, w.conv.id) is None
    w.customer.do_not_contact, w.cfg.auto_followup_enabled = False, False
    await test_db.commit()
    assert await FollowUpService.schedule_followup_if_eligible(test_db, w.tenant.id, w.lead.id, w.customer.id, w.conv.id) is None


# ----------------------------------------------------------------------------- channel rules
@pytest.mark.asyncio
async def test_whatsapp_closed_window_without_template_hands_the_draft_to_the_rep(test_db, fake_llm):
    w = await due(test_db, channel="WHATSAPP", whatsapp_phone_number_id="PN1", whatsapp_access_token=encrypt_secret("tok"))
    fake_llm.replies = [GOOD_DRAFT]
    text, template = AsyncMock(return_value=True), AsyncMock(return_value=True)
    with patch.object(WhatsAppService, "send_whatsapp_message", text), patch.object(WhatsAppService, "send_whatsapp_template", template):
        assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 0
    text.assert_not_awaited()
    template.assert_not_awaited()
    await test_db.refresh(w.sched)
    assert w.sched.status == "NEEDS_REP" and w.sched.custom_message == GOOD_DRAFT
    n = await notifs(test_db, w)
    assert len(n) == 1 and "24h window" in n[0].title and GOOD_DRAFT in n[0].message
    assert len(await all_schedules(test_db, w)) == 1


@pytest.mark.asyncio
async def test_whatsapp_closed_window_uses_the_approved_template_when_configured(test_db, fake_llm):
    w = await due(test_db, channel="WHATSAPP", whatsapp_phone_number_id="PN1", whatsapp_access_token=encrypt_secret("tok"),
                  whatsapp_followup_template="follow_up_v1")
    fake_llm.replies = [GOOD_DRAFT]
    template = AsyncMock(return_value=True)
    with patch.object(WhatsAppService, "send_whatsapp_template", template):
        assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 1
    phone_id, token, to, name, params = template.await_args.args
    assert (phone_id, token, to, name, params) == ("PN1", "tok", "+15551110000", "follow_up_v1", ["Jordan"])


@pytest.mark.asyncio
async def test_whatsapp_open_window_sends_free_text(test_db, fake_llm):
    w = await due(test_db, channel="WHATSAPP", whatsapp_phone_number_id="PN1", whatsapp_access_token=encrypt_secret("tok"),
                  last_customer_msg_age=timedelta(hours=2))
    fake_llm.replies = [GOOD_DRAFT]
    text = AsyncMock(return_value=True)
    with patch.object(WhatsAppService, "send_whatsapp_message", text):
        assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 1
    assert text.await_args.args == ("PN1", "tok", "+15551110000", GOOD_DRAFT)


@pytest.mark.asyncio
async def test_telegram_followup_and_delivery_failure_handling(test_db, fake_llm):
    w = await due(test_db, channel="TELEGRAM")
    w.customer.phone = "+tg_424242"
    await test_db.commit()
    fake_llm.replies = [GOOD_DRAFT]
    sent = AsyncMock(return_value=False)  # Telegram API down
    with patch.object(telegram_service, "send_message", sent):
        assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 0
    assert sent.await_args.args == ("424242", GOOD_DRAFT)
    await test_db.refresh(w.sched)
    assert (w.sched.status, w.sched.stop_reason) == ("FAILED", "DELIVERY_FAILED")
    assert (await messages(test_db, w.conv))[-1][1] != GOOD_DRAFT  # never records a message the customer did not get
    assert any("could not be delivered" in n.title for n in await notifs(test_db, w))


# ----------------------------------------------------------------------------- timing
@pytest.mark.asyncio
async def test_followups_wait_for_the_tenants_sending_window(test_db, fake_llm, monkeypatch):
    w = await due(test_db, tz="Asia/Kolkata")
    fake_llm.replies = [GOOD_DRAFT]

    monkeypatch.setattr(follow_up_service, "_utcnow", lambda: datetime(2026, 12, 15, 18, 0, tzinfo=timezone.utc))  # 23:30 IST
    assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 0
    await test_db.refresh(w.sched)
    assert w.sched.status == "PENDING" and w.sched.scheduled_at.replace(tzinfo=timezone.utc) == datetime(2026, 12, 16, 3, 30, tzinfo=timezone.utc)
    assert not fake_llm.calls

    w.sched.scheduled_at = NOW() - timedelta(minutes=1)
    await test_db.commit()
    monkeypatch.setattr(follow_up_service, "_utcnow", lambda: datetime(2026, 12, 16, 6, 0, tzinfo=timezone.utc))  # 11:30 IST
    assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 1


@pytest.mark.asyncio
async def test_one_scheduler_pass_serves_every_tenant_and_recovers_stuck_items(test_db):
    a, b = await due(test_db), await due(test_db)
    b.sched.status, b.sched.locked_at = "PROCESSING", NOW() - timedelta(minutes=30)  # a worker died mid-send
    await test_db.commit()
    assert await FollowUpService.process_due_followups(test_db) >= 2  # tenant_id=None → all tenants
    for w in (a, b):
        await test_db.refresh(w.sched)
        assert w.sched.status == "DISPATCHED"


# ----------------------------------------------------------------------------- human approval mode
@pytest.mark.asyncio
async def test_approval_mode_waits_for_a_rep_then_sends_the_edited_text(client, test_db, fake_llm):
    w = await due(test_db, followup_mode="APPROVAL")
    fake_llm.replies = [GOOD_DRAFT]
    assert await FollowUpService.process_due_followups(test_db, w.tenant.id) == 0
    await test_db.refresh(w.sched)
    assert w.sched.status == "AWAITING_APPROVAL" and w.sched.custom_message == GOOD_DRAFT
    assert all(c != GOOD_DRAFT for _, c in await messages(test_db, w.conv))  # nothing sent yet

    h = await auth_headers(test_db, w.tenant, role=__import__("app.models.user", fromlist=["UserRole"]).UserRole.SALES_USER)
    edited = "Hi Jordan! Still keen on the CRM? I can hold a demo slot for Thursday."
    res = await client.post(f"/api/v1/tenant-admin/follow-ups/{w.sched.id}/approve", headers=h, json={"message": edited})
    assert res.status_code == 200 and res.json()["sent"] is True
    await test_db.refresh(w.sched)
    assert w.sched.status == "DISPATCHED" and w.sched.custom_message == edited
    assert (await messages(test_db, w.conv))[-1] == ("AI", edited)
    assert (await client.post(f"/api/v1/tenant-admin/follow-ups/{w.sched.id}/approve", headers=h, json={})).status_code == 404  # no double send


@pytest.mark.asyncio
async def test_rep_can_reject_a_draft_and_other_tenants_cannot_touch_it(client, test_db, fake_llm):
    w = await due(test_db, followup_mode="APPROVAL")
    await FollowUpService.process_due_followups(test_db, w.tenant.id)
    stranger = await auth_headers(test_db, await make_tenant(test_db, "Stranger"))
    assert (await client.post(f"/api/v1/tenant-admin/follow-ups/{w.sched.id}/reject", headers=stranger)).status_code == 404

    h = await auth_headers(test_db, w.tenant)
    assert (await client.post(f"/api/v1/tenant-admin/follow-ups/{w.sched.id}/reject", headers=h)).json() == {"success": True}
    await test_db.refresh(w.sched)
    assert (w.sched.status, w.sched.stop_reason) == ("CANCELLED", "REJECTED_BY_REP")


@pytest.mark.asyncio
async def test_follow_up_listing_exposes_the_new_lifecycle_fields(client, test_db):
    w = await due(test_db, max_attempts=2)
    h = await auth_headers(test_db, w.tenant)
    items = (await client.get("/api/v1/tenant-admin/follow-ups", headers=h)).json()
    assert items[0]["max_attempts"] == 2 and items[0]["channel"] == "WEB_SIMULATOR" and items[0]["stop_reason"] is None
    assert (await client.post("/api/v1/tenant-admin/follow-ups/process-due", headers=h)).json() == {"success": True, "dispatched_count": 1}
