"""Channels (WhatsApp webhook hardening), the durable job queue, secrets at rest, AI config and observability APIs."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, is_encrypted
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.ai_job import AIJob
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.knowledge import KnowledgeDocument
from app.models.lead import Lead
from app.services import jobs
from app.services.ai import llm as llm_mod
from app.services.channels import whatsapp_credentials
from app.services.jobs import JobQueue, lane_key, run_one, run_pending
from app.services.rag_service import RAGService
from app.services.whatsapp_service import WhatsAppService, extract_messages, verify_signature
from tests.factories import (
    auth_headers, make_config, make_conversation, make_customer, make_product, make_tenant, session_factory,
)

WA = "/api/v1/webhooks/whatsapp"


@pytest.fixture(autouse=True)
async def _clean_queue(test_db):
    """The in-memory test database is shared across tests; start each one with an empty queue."""
    from sqlalchemy import delete

    from app.models.channel import InboundEvent

    await test_db.execute(delete(AIJob))
    await test_db.execute(delete(InboundEvent))
    await test_db.commit()


def wa_payload(phone_id, *messages):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone_id},
        "contacts": [{"wa_id": m[1], "profile": {"name": "Alice"}} for m in messages],
        "messages": [{"id": m[0], "from": m[1], "type": "text", "text": {"body": m[2]}} for m in messages],
    }}]}]}


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def post_wa(client, payload, secret=None, signature=None):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret or signature:
        headers["X-Hub-Signature-256"] = signature or sign(body, secret)
    return await client.post(WA, content=body, headers=headers)


@pytest.fixture()
async def wa_tenant(test_db):
    tenant = await make_tenant(test_db, "WA Co")
    cfg = await make_config(
        test_db, tenant, whatsapp_phone_number_id=f"PN-{tenant.slug}",
        whatsapp_access_token=encrypt_secret("EAAG-token"), whatsapp_app_secret=encrypt_secret("s3cret"),
    )
    return tenant, cfg


async def count_jobs(db, tenant, job_type="INBOUND_MESSAGE"):
    return len((await db.execute(select(AIJob).where(AIJob.tenant_id == tenant.id, AIJob.job_type == job_type))).scalars().all())


# ----------------------------------------------------------------------------- WhatsApp webhook
def test_signature_primitive_and_payload_flattening():
    body = b'{"a":1}'
    assert verify_signature(body, sign(body, "k"), "k")
    assert not verify_signature(body, sign(body, "other"), "k")
    assert not verify_signature(body, None, "k") and not verify_signature(body, sign(body, "k"), None)

    payload = wa_payload("P", ("m1", "491", "hi"), ("m2", "492", "yo"))
    payload["entry"][0]["changes"][0]["value"]["messages"].append({"id": "m3", "from": "493", "type": "image"})
    assert [m["message_id"] for m in extract_messages(payload)] == ["m1", "m2"]  # media ignored, all texts kept


@pytest.mark.asyncio
async def test_bad_signature_is_rejected_and_good_one_is_queued(client, test_db, wa_tenant):
    tenant, cfg = wa_tenant
    payload = wa_payload(cfg.whatsapp_phone_number_id, ("wamid.1", "15551230001", "Do you sell CRMs?"))

    assert (await post_wa(client, payload, signature="sha256=deadbeef")).status_code == 403
    assert (await post_wa(client, payload)).status_code == 403  # missing header while a secret is configured
    assert await count_jobs(test_db, tenant) == 0

    ok = await post_wa(client, payload, secret="s3cret")
    assert ok.status_code == 200 and ok.json()["queued"] == 1
    assert await count_jobs(test_db, tenant) == 1


@pytest.mark.asyncio
async def test_retried_deliveries_are_acknowledged_but_processed_once(client, test_db, wa_tenant):
    tenant, cfg = wa_tenant
    payload = wa_payload(cfg.whatsapp_phone_number_id, ("wamid.dup", "15551230002", "hello"))
    first = await post_wa(client, payload, secret="s3cret")
    again = await post_wa(client, payload, secret="s3cret")
    assert first.json()["queued"] == 1 and again.status_code == 200 and again.json()["duplicates"] == 1
    assert await count_jobs(test_db, tenant) == 1


@pytest.mark.asyncio
async def test_unknown_phone_number_is_never_routed_to_another_tenant(client, test_db, wa_tenant):
    tenant, cfg = wa_tenant
    res = await post_wa(client, wa_payload("SOMEONE-ELSE", ("wamid.x", "15551230003", "hi")))
    assert res.status_code == 200 and res.json()["ignored"] == 1 and res.json()["queued"] == 0
    assert await count_jobs(test_db, tenant) == 0


@pytest.mark.asyncio
async def test_batched_webhook_queues_one_job_per_message(client, test_db, wa_tenant):
    tenant, cfg = wa_tenant
    res = await post_wa(client, wa_payload(cfg.whatsapp_phone_number_id, ("w.a", "15551230004", "one"), ("w.b", "15551230005", "two")),
                        secret="s3cret")
    assert res.json()["queued"] == 2 and await count_jobs(test_db, tenant) == 2


@pytest.mark.asyncio
async def test_production_refuses_unsigned_webhooks(client, test_db, monkeypatch):
    tenant = await make_tenant(test_db, "NoSecret Co")
    cfg = await make_config(test_db, tenant, whatsapp_phone_number_id="PN-NOSECRET")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", "")
    res = await post_wa(client, wa_payload("PN-NOSECRET", ("w.p", "15551230006", "hi")))
    assert res.status_code == 403
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert (await post_wa(client, wa_payload("PN-NOSECRET", ("w.q", "15551230006", "hi")))).status_code == 200


@pytest.mark.asyncio
async def test_worker_turns_a_webhook_message_into_a_delivered_reply_and_a_lead(client, test_db, wa_tenant, fake_llm):
    tenant, cfg = wa_tenant
    await make_product(test_db, tenant, name="Enterprise CRM", price=999.0)
    fake_llm.replies = ["Yes, the Enterprise CRM is $999 per month. Want a demo?"]
    fake_llm.analysis = {"intent": "demo_request", "buying_intent": True, "budget": 999}
    await post_wa(client, wa_payload(cfg.whatsapp_phone_number_id, ("wamid.e2e", "15551239999", "Do you sell CRMs? Price?")), secret="s3cret")

    order = []
    send = AsyncMock(side_effect=lambda *a, **k: order.append("send") or True)
    real_analyze = fake_llm.__class__._complete

    def tracking(self, task, messages, kwargs):
        if kwargs.get("response_format"):
            order.append("analysis")
        return real_analyze(self, task, messages, kwargs)

    with patch.object(WhatsAppService, "send_whatsapp_message", send), patch.object(fake_llm.__class__, "_complete", tracking):
        assert await run_pending(session_factory(test_db)) == 1

    send.assert_awaited_once()
    phone_id, token, to, text = send.await_args.args
    assert (phone_id, token, to) == (cfg.whatsapp_phone_number_id, "EAAG-token", "15551239999")  # token decrypted for use
    assert "$999" in text
    assert order == ["send", "analysis"]  # the customer gets the answer BEFORE slow analysis runs

    cust = (await test_db.execute(select(Customer).where(Customer.phone == "15551239999"))).scalar_one()
    assert cust.name == "Alice"
    conv = (await test_db.execute(select(Conversation).where(Conversation.customer_id == cust.id))).scalar_one()
    assert conv.channel == "WHATSAPP"
    assert (await test_db.execute(select(Lead).where(Lead.conversation_id == conv.id))).scalar_one().score >= 60


@pytest.mark.asyncio
async def test_messages_for_a_human_handled_conversation_are_stored_not_answered(client, test_db, wa_tenant, fake_llm):
    tenant, cfg = wa_tenant
    cust = await make_customer(test_db, tenant, phone="15551230077")
    conv = await make_conversation(test_db, tenant, cust, channel="WHATSAPP", status="ESCALATED_HUMAN")
    await post_wa(client, wa_payload(cfg.whatsapp_phone_number_id, ("wamid.h", "15551230077", "Are you there?")), secret="s3cret")
    send = AsyncMock(return_value=True)
    with patch.object(WhatsAppService, "send_whatsapp_message", send):
        await run_pending(session_factory(test_db))
    send.assert_not_awaited()
    assert not fake_llm.calls
    msgs = (await test_db.execute(select(Message).where(Message.conversation_id == conv.id))).scalars().all()
    assert [m.content for m in msgs] == ["Are you there?"]


# ----------------------------------------------------------------------------- job queue
@pytest.mark.asyncio
async def test_jobs_of_one_customer_never_run_concurrently(test_db):
    t = await make_tenant(test_db)
    lane = lane_key(t.id, "+15550000001")
    a = await JobQueue.enqueue(test_db, "X", {"n": 1}, tenant_id=t.id, conversation_id=lane)
    b = await JobQueue.enqueue(test_db, "X", {"n": 2}, tenant_id=t.id, conversation_id=lane)
    other = await JobQueue.enqueue(test_db, "X", {"n": 3}, tenant_id=t.id, conversation_id=lane_key(t.id, "+15550000002"))

    first = await JobQueue.claim_next(test_db)
    assert first.id == a.id and first.status == "RUNNING" and first.attempts == 1
    second = await JobQueue.claim_next(test_db)
    assert second.id == other.id  # b is blocked behind a; a different customer's job proceeds
    assert await JobQueue.claim_next(test_db) is None
    await JobQueue.finish(test_db, first)
    assert (await JobQueue.claim_next(test_db)).id == b.id


@pytest.mark.asyncio
async def test_failed_jobs_retry_with_backoff_then_stop(test_db):
    calls = []

    @jobs.register_handler("BOOM")
    async def boom(db, job):
        calls.append(job.attempts)
        raise RuntimeError("kaput")

    factory = session_factory(test_db)
    job = await JobQueue.enqueue(test_db, "BOOM", {}, max_attempts=3)
    assert await run_one(factory) is True
    await test_db.refresh(job)
    assert job.status == "PENDING" and job.attempts == 1 and "kaput" in job.last_error
    assert job.run_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)  # backed off
    assert await run_one(factory) is False  # not due yet

    for _ in range(2):
        job.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await test_db.commit()
        await run_one(factory)
    await test_db.refresh(job)
    assert job.status == "FAILED" and calls == [1, 2, 3]
    jobs._HANDLERS.pop("BOOM")


@pytest.mark.asyncio
async def test_jobs_stuck_running_after_a_crash_are_recovered(test_db):
    job = await JobQueue.enqueue(test_db, "X", {})
    job.status, job.locked_at = "RUNNING", datetime.now(timezone.utc) - timedelta(minutes=10)
    await test_db.commit()
    claimed = await JobQueue.claim_next(test_db)
    assert claimed.id == job.id and claimed.attempts == 1


@pytest.mark.asyncio
async def test_unknown_job_types_fail_loudly_instead_of_vanishing(test_db):
    job = await JobQueue.enqueue(test_db, "NOPE", {}, max_attempts=1)
    await run_one(session_factory(test_db))
    await test_db.refresh(job)
    assert job.status == "FAILED" and "No handler" in job.last_error


# ----------------------------------------------------------------------------- secrets + AI config
@pytest.mark.asyncio
async def test_secrets_are_encrypted_at_rest_and_never_returned(client, test_db):
    tenant = await make_tenant(test_db)
    h = await auth_headers(test_db, tenant)
    res = await client.put("/api/v1/tenant-admin/ai-config", headers=h, json={
        "groq_api_key": "gsk_abc123XYZ", "whatsapp_access_token": "EAAG-live-token", "whatsapp_app_secret": "app-secret",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["has_groq_api_key"] and body["has_whatsapp_access_token"] and body["has_whatsapp_app_secret"]
    assert "gsk_abc123XYZ" not in res.text and "EAAG-live-token" not in res.text and "app-secret" not in res.text

    cfg = (await test_db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant.id))).scalar_one()
    for stored, plain in ((cfg.groq_api_key, "gsk_abc123XYZ"), (cfg.whatsapp_access_token, "EAAG-live-token"), (cfg.whatsapp_app_secret, "app-secret")):
        assert is_encrypted(stored) and plain not in stored and decrypt_secret(stored) == plain

    llm_mod.set_llm_override(None)  # use the real factory: it must decrypt the tenant key
    assert llm_mod.build_llm_client(cfg, task="reply").api_key == "gsk_abc123XYZ"
    assert whatsapp_credentials(cfg)[1] == "EAAG-live-token"

    # blank values / browser autofill / non-Groq-looking keys never overwrite a stored secret
    for bad in ({"groq_api_key": ""}, {"groq_api_key": "hunter2"}, {"whatsapp_access_token": "  "}):
        assert (await client.put("/api/v1/tenant-admin/ai-config", headers=h, json=bad)).status_code == 200
    await test_db.refresh(cfg)
    assert decrypt_secret(cfg.groq_api_key) == "gsk_abc123XYZ" and decrypt_secret(cfg.whatsapp_access_token) == "EAAG-live-token"


def test_legacy_plaintext_secrets_remain_readable_and_wrong_keys_fail_closed():
    assert decrypt_secret("plain-legacy-key") == "plain-legacy-key"
    assert decrypt_secret(None) is None
    assert decrypt_secret("enc:v1:not-a-valid-token") is None


@pytest.mark.asyncio
async def test_new_config_fields_roundtrip_and_validate(client, test_db):
    tenant = await make_tenant(test_db)
    h = await auth_headers(test_db, tenant)
    res = await client.put("/api/v1/tenant-admin/ai-config", headers=h, json={
        "language": "Hindi", "followup_mode": "APPROVAL", "max_followups": 2, "monthly_token_limit": 50000,
        "fast_model": "llama-3.1-8b-instant", "whatsapp_followup_template": "follow_up_v1", "qualification_threshold": 70,
    })
    assert res.status_code == 200
    got = (await client.get("/api/v1/tenant-admin/ai-config", headers=h)).json()
    assert (got["language"], got["followup_mode"], got["max_followups"], got["monthly_token_limit"], got["qualification_threshold"]) == ("Hindi", "APPROVAL", 2, 50000, 70)
    assert got["fast_model"] == "llama-3.1-8b-instant" and got["whatsapp_followup_template"] == "follow_up_v1"

    for bad in ({"followup_mode": "YOLO"}, {"monthly_token_limit": 10}, {"max_followups": 50}):
        assert (await client.put("/api/v1/tenant-admin/ai-config", headers=h, json=bad)).status_code == 422


@pytest.mark.asyncio
async def test_unused_settings_now_take_effect(client, test_db, fake_llm):
    """is_enabled, qualification_threshold and greeting_message used to be stored but ignored."""
    tenant = await make_tenant(test_db)
    h = await auth_headers(test_db, tenant)
    await client.put("/api/v1/tenant-admin/ai-config", headers=h, json={
        "greeting_message": "Namaste! Welcome to Acme.", "qualification_threshold": 90,
    })
    fake_llm.analysis = {"intent": "pricing_inquiry", "buying_intent": True}  # 50 + 10 = 60
    r = await client.post("/api/v1/tenant-admin/chat/simulate", headers=h, json={"message": "How much is it?", "customer_phone": "+15550001234"})
    assert "Namaste! Welcome to Acme." in fake_llm.reply_calls()[0]["messages"][0]["content"]
    assert r.json()["score"] == 60
    lead = (await test_db.execute(select(Lead).where(Lead.tenant_id == tenant.id))).scalar_one()
    assert lead.priority == "LOW" and lead.status == "NEW"  # threshold 90 → capture at 70 → a 60 is neither qualified nor medium

    await client.put("/api/v1/tenant-admin/ai-config", headers=h, json={"is_enabled": False})
    r2 = await client.post("/api/v1/tenant-admin/chat/simulate", headers=h, json={"message": "Anyone?", "customer_phone": "+15550001234"})
    assert r2.json()["reply"] == "" and len(fake_llm.reply_calls()) == 1


# ----------------------------------------------------------------------------- observability + knowledge/product APIs
@pytest.mark.asyncio
async def test_traces_api_exposes_what_the_ai_did_and_is_tenant_scoped(client, test_db, fake_llm):
    tenant, other = await make_tenant(test_db, "Trace A"), await make_tenant(test_db, "Trace B")
    h, h_other = await auth_headers(test_db, tenant), await auth_headers(test_db, other)
    await make_product(test_db, tenant, name="Enterprise CRM", price=999.0)
    fake_llm.replies = ["It's $999 per month."]
    await client.post("/api/v1/tenant-admin/chat/simulate", headers=h, json={"message": "price of the CRM?", "customer_phone": "+15550004321"})

    traces = (await client.get("/api/v1/tenant-admin/ai-usage/traces", headers=h)).json()
    inbound = [t for t in traces if t["trigger"] == "SIMULATOR"][0]
    assert inbound["model"] == "fake-model" and inbound["reply"] == "It's $999 per month."
    detail = (await client.get(f"/api/v1/tenant-admin/ai-usage/traces/{inbound['id']}", headers=h)).json()
    assert detail["retrieval"]["query"] == "price of the CRM?" and "context_ms" in detail["timings"]
    assert (await client.get(f"/api/v1/tenant-admin/ai-usage/traces/{inbound['id']}", headers=h_other)).status_code == 404
    assert (await client.get("/api/v1/tenant-admin/ai-usage/traces", headers=h_other)).json() == []

    stats = (await client.get("/api/v1/tenant-admin/ai-usage/stats", headers=h)).json()
    assert stats["total_tokens"] > 0 and "monthly_budget" in stats and stats["vector_backend"] in ("numpy", "pgvector")


@pytest.mark.asyncio
async def test_large_uploads_are_indexed_by_the_worker_and_can_be_reindexed(client, test_db, monkeypatch):
    tenant = await make_tenant(test_db)
    h = await auth_headers(test_db, tenant)
    monkeypatch.setattr(settings, "KNOWLEDGE_INLINE_MAX_CHARS", 50)
    text = b"# Warranty\nThe ZX-9000 has a two year warranty covering hardware faults and defects. " * 3

    up = await client.post("/api/v1/tenant-admin/knowledge/upload", headers=h, files={"file": ("big.md", text, "text/markdown")})
    assert up.status_code == 201 and up.json()["status"] == "PENDING" and up.json()["chunk_count"] >= 1
    assert (await client.post("/api/v1/tenant-admin/knowledge/test-search", headers=h, json={"query": "ZX-9000 warranty"})).json()["matches"] == []

    assert await run_pending(session_factory(test_db)) == 1
    docs = (await client.get("/api/v1/tenant-admin/knowledge/documents", headers=h)).json()["items"]
    assert docs[0]["status"] == "INDEXED"
    found = (await client.post("/api/v1/tenant-admin/knowledge/test-search", headers=h, json={"query": "ZX-9000 warranty"})).json()
    assert found["matches"] and found["confidence"] > 0 and found["matches"][0]["section"] == "Warranty"

    again = await client.post(f"/api/v1/tenant-admin/knowledge/documents/{docs[0]['id']}/reindex", headers=h)
    assert again.status_code == 200 and again.json()["status"] == "INDEXED"
    assert (await client.post("/api/v1/tenant-admin/knowledge/reindex", headers=h)).json() == {"queued": False, "stale_chunks": 0, "detail": {}}


@pytest.mark.asyncio
async def test_unreadable_and_unsupported_uploads_get_clear_errors(client, test_db):
    tenant = await make_tenant(test_db)
    h = await auth_headers(test_db, tenant)
    bad_pdf = await client.post("/api/v1/tenant-admin/knowledge/upload", headers=h, files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert bad_pdf.status_code == 400 and "Could not read" in bad_pdf.json()["detail"]
    assert (await client.post("/api/v1/tenant-admin/knowledge/upload", headers=h, files={"file": ("x.exe", b"MZ", "application/octet-stream")})).status_code == 400


@pytest.mark.asyncio
async def test_product_changes_keep_the_ai_catalog_current(client, test_db):
    tenant = await make_tenant(test_db)
    h = await auth_headers(test_db, tenant)
    created = await client.post("/api/v1/tenant-admin/products", headers=h, json={
        "name": "OmniSuite", "sku": "OMNI-9", "price": 100, "currency": "USD", "description": "All-in-one suite"})
    pid = created.json()["id"]

    async def hits(query="OMNI-9"):
        return (await RAGService.retrieve(test_db, tenant.id, query, source_type="PRODUCT")).matches

    assert hits() and "100.00" in (await hits())[0]["content"]
    await client.put(f"/api/v1/tenant-admin/products/{pid}", headers=h, json={"price": 250})
    assert "250.00" in (await hits())[0]["content"]
    await client.put(f"/api/v1/tenant-admin/products/{pid}", headers=h, json={"is_active": False})
    assert await hits() == []
    await client.put(f"/api/v1/tenant-admin/products/{pid}", headers=h, json={"is_active": True})
    assert await hits()
    assert (await client.delete(f"/api/v1/tenant-admin/products/{pid}", headers=h)).status_code == 204
    assert await hits() == []
