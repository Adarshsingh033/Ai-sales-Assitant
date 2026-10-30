"""
Tests for AI Sales Engine multi-turn conversation flow, anti-repetition, and SSE streaming.
"""
import json
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, patch

from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.core.security import create_access_token
from app.services.groq_client import GroqClient


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


@pytest.mark.asyncio
async def test_groq_client_stream_tokens():
    """Verify GroqClient.stream_chat_completion yields tokens from SSE stream."""
    client = GroqClient(api_key="mock_key")

    mock_sse_lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" there!"}}]}',
        'data: [DONE]',
    ]

    class MockStreamResponse:
        status_code = 200

        async def aiter_lines(self):
            for line in mock_sse_lines:
                yield line

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockAsyncClient:
        def stream(self, method, url, **kwargs):
            return MockStreamResponse()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.AsyncClient", return_value=MockAsyncClient()):
        tokens = []
        async for token in client.stream_chat_completion([{"role": "user", "content": "Hi"}]):
            tokens.append(token)

        assert "".join(tokens) == "Hello there!"


@pytest.mark.asyncio
async def test_simulate_stream_endpoint(client: AsyncClient, sales_test_tenant):
    """Verify POST /api/v1/tenant-admin/chat/simulate-stream returns SSE chunks and final event."""
    token, tenant, user = sales_test_tenant
    headers = {"Authorization": f"Bearer {token}"}

    # Configure Assistant name as Simaphore
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

    # Mock the Groq stream and chat completion
    async def mock_stream_gen(messages, **kwargs):
        yield "Welcome to "
        yield "our cloud suite!"

    with patch.object(GroqClient, "stream_chat_completion", side_effect=mock_stream_gen), \
         patch.object(GroqClient, "chat_completion", return_value=json.dumps({
             "intent": "product_inquiry",
             "buying_intent": True,
             "score": 75,
             "score_reason": "Customer inquired about cloud solutions",
             "needs_human_handoff": False,
         })):

        resp = await client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={
                "message": "i want some ai and cloud solution",
                "customer_name": "Alex Mercer",
                "customer_phone": "+15550199001",
            },
        )

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        body_text = resp.text
        assert "data: " in body_text

        # Parse SSE lines
        tokens = []
        done_payload = None
        for line in body_text.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "token" in data:
                    tokens.append(data["token"])
                if data.get("done"):
                    done_payload = data

        assert "".join(tokens) == "Welcome to our cloud suite!"
        assert done_payload is not None
        assert done_payload["done"] is True
        assert done_payload["session_id"] is not None
        assert done_payload["score"] == 75
        assert done_payload["intent"] == "product_inquiry"


@pytest.mark.asyncio
async def test_anti_repetition_multi_turn_prompting(client: AsyncClient, sales_test_tenant):
    """Verify prompt rules instruct LLM not to repeat greetings and advance sale on 'ok'."""
    token, tenant, user = sales_test_tenant
    headers = {"Authorization": f"Bearer {token}"}

    captured_messages = []

    async def mock_stream(messages, **kwargs):
        captured_messages.append(messages)
        yield "I can help with that."

    with patch.object(GroqClient, "stream_chat_completion", side_effect=mock_stream), \
         patch.object(GroqClient, "chat_completion", return_value=json.dumps({
             "intent": "general_inquiry",
             "buying_intent": True,
             "score": 80,
             "score_reason": "High intent",
             "needs_human_handoff": False,
         })):

        # Turn 1: Initial message
        r1 = await client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={"message": "i want some ai and cloud solution", "customer_phone": "+15550199002"},
        )
        assert r1.status_code == 200
        done1 = [json.loads(l[6:]) for l in r1.text.split("\n") if l.startswith("data: ") and "done" in l][0]
        session_id = done1["session_id"]

        # Check turn 1 prompt: is_first_turn instruction
        first_prompt = captured_messages[0][0]["content"]
        assert "start of the conversation" in first_prompt

        # Turn 2: "from where i can buy?" with session_id
        r2 = await client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={"message": "from where i can buy?", "session_id": session_id, "customer_phone": "+15550199002"},
        )
        assert r2.status_code == 200

        # Turn 3: "ok" with session_id
        r3 = await client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={"message": "ok", "session_id": session_id, "customer_phone": "+15550199002"},
        )
        assert r3.status_code == 200

        # Check turn 3 prompt: ongoing conversation instruction
        third_prompt = captured_messages[2][0]["content"]
        assert "This is an ONGOING conversation. DO NOT say 'Hi there!'" in third_prompt
        assert "ACKNOWLEDGMENTS & SHORT REPLIES" in third_prompt
        assert "DO NOT reset the conversation" in third_prompt


@pytest.mark.asyncio
async def test_human_takeover_pauses_ai_and_allows_human_reply(client: AsyncClient, sales_test_tenant):
    """Verify human takeover pauses AI in chat simulator and representative replies pass through."""
    token, tenant, user = sales_test_tenant
    headers = {"Authorization": f"Bearer {token}"}

    groq_stream_called = 0

    async def mock_stream(messages, **kwargs):
        nonlocal groq_stream_called
        groq_stream_called += 1
        yield "Hello! How can I assist you with products?"

    with patch.object(GroqClient, "stream_chat_completion", side_effect=mock_stream), \
         patch.object(GroqClient, "chat_completion", return_value=json.dumps({
             "intent": "product_inquiry",
             "buying_intent": True,
             "score": 50,
             "score_reason": "Product interest",
             "needs_human_handoff": False,
         })):

        # 1. Customer initiates chat
        r1 = await client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={"message": "I want pricing for software", "customer_phone": "+15550199099"},
        )
        assert r1.status_code == 200
        assert groq_stream_called == 1
        done1 = [json.loads(l[6:]) for l in r1.text.split("\n") if l.startswith("data: ") and "done" in l][0]
        session_id = done1["session_id"]

        # 2. Human representative takes over
        r_takeover = await client.post(
            f"/api/v1/tenant-admin/conversations/{session_id}/takeover",
            headers=headers,
        )
        assert r_takeover.status_code == 200
        assert r_takeover.json()["status"] == "ESCALATED_HUMAN"

        # 3. Human representative sends a reply
        r_reply = await client.post(
            f"/api/v1/tenant-admin/conversations/{session_id}/reply",
            headers=headers,
            json={"content": "Hi! I am Marcus from sales. How many licenses do you need?"},
        )
        assert r_reply.status_code == 200
        assert r_reply.json()["sender_type"] == "HUMAN_AGENT"

        # 4. Verify conversation messages include the HUMAN_AGENT reply
        r_msgs = await client.get(
            f"/api/v1/tenant-admin/conversations/{session_id}/messages",
            headers=headers,
        )
        assert r_msgs.status_code == 200
        msgs = r_msgs.json()
        assert len(msgs) == 3
        assert msgs[0]["sender_type"] == "CUSTOMER"
        assert msgs[1]["sender_type"] == "AI"
        assert msgs[2]["sender_type"] == "HUMAN_AGENT"
        assert msgs[2]["content"] == "Hi! I am Marcus from sales. How many licenses do you need?"

        # 5. Customer sends a question while human takeover is active
        r_cust2 = await client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={"message": "We need 15 licenses", "session_id": session_id, "customer_phone": "+15550199099"},
        )
        assert r_cust2.status_code == 200
        # AI Groq stream MUST NOT be called!
        assert groq_stream_called == 1

        # Response must indicate human_takeover: True and empty AI reply
        events2 = [json.loads(l[6:]) for l in r_cust2.text.split("\n") if l.startswith("data: ") and "done" in l]
        assert len(events2) == 1
        assert events2[0]["human_takeover"] is True
        assert events2[0]["reply"] == ""

        # 6. Verify customer's message is saved and NO new AI message exists
        r_msgs2 = await client.get(
            f"/api/v1/tenant-admin/conversations/{session_id}/messages",
            headers=headers,
        )
        msgs2 = r_msgs2.json()
        assert len(msgs2) == 4
        assert msgs2[3]["sender_type"] == "CUSTOMER"
        assert msgs2[3]["content"] == "We need 15 licenses"

        # 7. Handback to AI Assistant
        r_handback = await client.post(
            f"/api/v1/tenant-admin/conversations/{session_id}/handback",
            headers=headers,
        )
        assert r_handback.status_code == 200
        assert r_handback.json()["status"] == "ACTIVE_AI"

        # 8. Customer sends next message -> AI should answer again
        r_cust3 = await client.post(
            "/api/v1/tenant-admin/chat/simulate-stream",
            headers=headers,
            json={"message": "Are you AI or human now?", "session_id": session_id, "customer_phone": "+15550199099"},
        )
        assert r_cust3.status_code == 200
        assert groq_stream_called == 2
