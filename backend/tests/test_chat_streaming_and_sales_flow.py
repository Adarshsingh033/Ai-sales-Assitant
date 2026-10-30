"""
Tests for AI Sales Engine multi-turn conversation flow, anti-repetition prompting, SSE streaming and human takeover.
"""
import json
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.services.ai.llm import LLMClient


@pytest.fixture()
async def sales_test_tenant(test_db: AsyncSession):
    uid = uuid.uuid4().hex[:6]
    tenant = Tenant(name=f"Sales Flow Tenant {uid}", slug=f"sales-flow-{uid}")
    test_db.add(tenant)
    await test_db.flush()

    user = User(
        first_name="Marcus",
        last_name="Vance",
        email=f"marcus_{uid}@salesflow.com",
        password_hash="hashed_pw",
        role=UserRole.TENANT_ADMIN,
        tenant_id=tenant.id,
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()

    token = create_access_token(str(user.id), user.role.value, str(tenant.id))
    return token, tenant, user


def _sse_events(body: str):
    return [json.loads(line[6:]) for line in body.split("\n") if line.startswith("data: ")]


@pytest.mark.asyncio
async def test_llm_client_streams_tokens_and_usage():
    """The gateway yields visible tokens from an SSE stream and reports real usage from the final chunk."""
    sse = (
        'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":" there!"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"x_groq":{"usage":{"prompt_tokens":42,"completion_tokens":3}}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = LLMClient(api_key="k", model="m", http=http, fallback_models=[])

    tokens, done = [], None
    async for ev in client.stream([{"role": "user", "content": "Hi"}]):
        if ev.kind == "token":
            tokens.append(ev.text)
        else:
            done = ev.result
    assert "".join(tokens) == "Hello there!"
    assert done.usage.prompt_tokens == 42 and done.usage.completion_tokens == 3
    assert done.usage.estimated is False


@pytest.mark.asyncio
async def test_simulate_stream_endpoint(client: AsyncClient, sales_test_tenant, fake_llm):
    """POST /chat/simulate-stream returns SSE token chunks and a final event with the checked reply and lead signals."""
    token, tenant, user = sales_test_tenant
    headers = {"Authorization": f"Bearer {token}"}

    await client.put(
        "/api/v1/tenant-admin/ai-config",
        headers=headers,
        json={
            "assistant_name": "Simaphore",
            "personality": "Friendly",
            "greeting_message": "Hi! Welcome to EWW, how can I help you?",
            "custom_instructions": "Help customers find AI and cloud software.",
        },
    )
    fake_llm.replies = ["Welcome to our cloud suite!"]
    fake_llm.analysis = {"intent": "product_inquiry", "buying_intent": True}

    resp = await client.post(
        "/api/v1/tenant-admin/chat/simulate-stream",
        headers=headers,
        json={"message": "i want some ai and cloud solution", "customer_name": "Alex Mercer", "customer_phone": "+15550199001"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    events = _sse_events(resp.text)
    tokens = [e["token"] for e in events if "token" in e]
    done = next(e for e in events if e.get("done"))
    assert "".join(tokens) == "Welcome to our cloud suite!"
    assert done["reply"] == "Welcome to our cloud suite!"
    assert done["session_id"] is not None
    assert done["intent"] == "product_inquiry"
    assert 40 <= done["score"] <= 60  # product inquiry 38 + buying intent 10
    assert done["lead_id"] is not None


@pytest.mark.asyncio
async def test_system_prompt_enforces_anti_repetition_and_persona(client: AsyncClient, sales_test_tenant, fake_llm):
    """First turn greets; later turns must not re-introduce; short acknowledgments must advance the sale."""
    token, tenant, user = sales_test_tenant
    headers = {"Authorization": f"Bearer {token}"}
    fake_llm.analysis = {"intent": "general_inquiry", "buying_intent": True}

    def say(message: str, session_id=None):
        return client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={"message": message, "session_id": session_id, "customer_phone": "+15550199002"},
        )

    r1 = await say("i want some ai and cloud solution")
    session_id = next(e for e in _sse_events(r1.text) if e.get("done"))["session_id"]
    await say("from where i can buy?", session_id)
    await say("ok", session_id)

    prompts = [c["messages"][0]["content"] for c in fake_llm.reply_calls()]
    assert len(prompts) == 3
    assert "start of the conversation" in prompts[0]
    assert "This is an ONGOING conversation. DO NOT say 'Hi there!'" in prompts[2]
    assert "ACKNOWLEDGMENTS & SHORT REPLIES" in prompts[2]
    assert "DO NOT reset the conversation" in prompts[2]
    # conversation history is replayed in order on later turns
    last_roles = [m["role"] for m in fake_llm.reply_calls()[2]["messages"][1:]]
    assert last_roles == ["user", "assistant", "user", "assistant", "user"]


@pytest.mark.asyncio
async def test_human_takeover_pauses_ai_and_allows_human_reply(client: AsyncClient, sales_test_tenant, fake_llm):
    """Human takeover pauses the AI in the simulator and the rep's reply passes through."""
    token, tenant, user = sales_test_tenant
    headers = {"Authorization": f"Bearer {token}"}
    fake_llm.replies = ["Hello! How can I assist you with products?", "Still me, the assistant."]
    fake_llm.analysis = {"intent": "product_inquiry"}

    r1 = await client.post(
        "/api/v1/tenant-admin/chat/simulate-stream",
        headers=headers,
        json={"message": "I want pricing for software", "customer_phone": "+15550199099"},
    )
    assert r1.status_code == 200
    assert len(fake_llm.reply_calls()) == 1
    session_id = next(e for e in _sse_events(r1.text) if e.get("done"))["session_id"]

    r_takeover = await client.post(f"/api/v1/tenant-admin/conversations/{session_id}/takeover", headers=headers)
    assert r_takeover.status_code == 200
    assert r_takeover.json()["status"] == "ESCALATED_HUMAN"

    r_reply = await client.post(
        f"/api/v1/tenant-admin/conversations/{session_id}/reply",
        headers=headers,
        json={"content": "Hi! I am Marcus from sales. How many licenses do you need?"},
    )
    assert r_reply.status_code == 200
    assert r_reply.json()["sender_type"] == "HUMAN_AGENT"

    msgs = (await client.get(f"/api/v1/tenant-admin/conversations/{session_id}/messages", headers=headers)).json()
    assert [m["sender_type"] for m in msgs] == ["CUSTOMER", "AI", "HUMAN_AGENT"]

    # Customer writes while the human is active: AI must stay silent
    r_cust2 = await client.post(
        "/api/v1/tenant-admin/chat/simulate-stream",
        headers=headers,
        json={"message": "We need 15 licenses", "session_id": session_id, "customer_phone": "+15550199099"},
    )
    assert len(fake_llm.reply_calls()) == 1
    ev = [e for e in _sse_events(r_cust2.text) if e.get("done")]
    assert len(ev) == 1 and ev[0]["human_takeover"] is True and ev[0]["reply"] == ""
    msgs2 = (await client.get(f"/api/v1/tenant-admin/conversations/{session_id}/messages", headers=headers)).json()
    assert len(msgs2) == 4 and msgs2[3]["sender_type"] == "CUSTOMER" and msgs2[3]["content"] == "We need 15 licenses"

    # Hand back → the AI answers again
    assert (await client.post(f"/api/v1/tenant-admin/conversations/{session_id}/handback", headers=headers)).json()["status"] == "ACTIVE_AI"
    r_cust3 = await client.post(
        "/api/v1/tenant-admin/chat/simulate-stream",
        headers=headers,
        json={"message": "Are you AI or human now?", "session_id": session_id, "customer_phone": "+15550199099"},
    )
    assert r_cust3.status_code == 200
    assert len(fake_llm.reply_calls()) == 2
