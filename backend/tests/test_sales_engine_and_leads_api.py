"""
End-to-End Integration Tests for AI Sales Engine, WhatsApp Webhook,
Chat Simulator, Leads Pipeline, Human Takeover, and Dashboard Stats.
"""
from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.product import Product
from app.core.security import create_access_token
from app.core.config import settings
from app.services.rag_service import RAGService


@pytest.fixture()
async def tenant_and_user_token(test_db: AsyncSession):
    import uuid
    uid = uuid.uuid4().hex[:6]
    tenant = Tenant(name=f"Sales Cloud {uid}", slug=f"sales-cloud-{uid}")
    test_db.add(tenant)
    await test_db.flush()

    user = User(
        first_name="Sam",
        last_name="Seller",
        email=f"sam_{uid}@salescloud.com",
        password_hash="fake",
        role=UserRole.TENANT_ADMIN,
        tenant_id=tenant.id,
        is_active=True,
    )
    test_db.add(user)

    # Add sample product
    product = Product(
        tenant_id=tenant.id,
        name="Enterprise CRM Suite",
        sku="CRM-ENT",
        price=999.00,
        currency="USD",
        category="Software",
        description="Full-featured enterprise CRM platform.",
    )
    test_db.add(product)
    await test_db.commit()

    token = create_access_token(str(user.id), user.role.value, str(tenant.id))
    return token, tenant, user


@pytest.mark.asyncio
async def test_whatsapp_webhook_verification(client: AsyncClient):
    # Valid verification
    resp_valid = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "1234567890",
            "hub.verify_token": settings.WHATSAPP_VERIFY_TOKEN,
        },
    )
    assert resp_valid.status_code == 200
    assert resp_valid.text == "1234567890"

    # Invalid token
    resp_invalid = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "1234567890",
            "hub.verify_token": "wrong_token",
        },
    )
    assert resp_invalid.status_code == 403


@pytest.mark.asyncio
async def test_chat_simulation_lead_capture_and_scoring(client: AsyncClient, tenant_and_user_token):
    token, tenant, user = tenant_and_user_token

    # Mock Groq replies
    mock_reply = "We offer the Enterprise CRM Suite at $999/month. Would you like to schedule a quick demo?"
    mock_intel_json = (
        '{"intent": "pricing_inquiry", "buying_intent": true, "budget": 999.0, '
        '"timeline": "immediate", "requirements": "Enterprise CRM with demo", '
        '"score": 85, "score_reason": "Confirmed budget and requested demo.", '
        '"needs_human_handoff": false}'
    )

    with patch("app.services.groq_client.GroqClient.chat_completion") as mock_chat:
        mock_chat.side_effect = [mock_reply, mock_intel_json]

        # Customer sends first inquiry
        sim_resp = await client.post(
            "/api/v1/tenant-admin/chat/simulate",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "Hi, what is the price of Enterprise CRM Suite? We have budget for 10 users.",
                "customer_name": "Alice Johnson",
                "customer_phone": "+15559876543",
            },
        )
        assert sim_resp.status_code == 200
        data = sim_resp.json()
        assert data["reply"] == mock_reply
        assert data["score"] == 85
        assert data["lead_id"] is not None
        session_id = data["session_id"]

        # Check conversation history
        hist_resp = await client.get(
            f"/api/v1/tenant-admin/chat/history/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert hist_resp.status_code == 200
        messages = hist_resp.json()
        assert len(messages) == 2
        assert messages[0]["sender_type"] == "CUSTOMER"
        assert messages[1]["sender_type"] == "AI"


@pytest.mark.asyncio
async def test_leads_pipeline_stats_and_updates(client: AsyncClient, tenant_and_user_token):
    token, tenant, user = tenant_and_user_token

    # Create lead via chat simulation
    with patch("app.services.groq_client.GroqClient.chat_completion") as mock_chat:
        mock_chat.side_effect = [
            "Certainly!",
            '{"intent": "quote_request", "buying_intent": true, "budget": 5000.0, "timeline": "within 30 days", "requirements": "Custom setup", "score": 90, "score_reason": "High intent quote request", "needs_human_handoff": false}',
        ]
        await client.post(
            "/api/v1/tenant-admin/chat/simulate",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "I need a quote for enterprise plan.", "customer_name": "Bob Prospect"},
        )

    # 1. Pipeline stats
    stats_resp = await client.get(
        "/api/v1/tenant-admin/leads/pipeline-stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_leads"] >= 1
    assert stats["total_estimated_value"] >= 5000.0

    # 2. List leads
    leads_resp = await client.get(
        "/api/v1/tenant-admin/leads",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert leads_resp.status_code == 200
    lead_items = leads_resp.json()["items"]
    assert len(lead_items) >= 1
    lead_id = lead_items[0]["id"]
    assert lead_items[0]["score"] == 90

    # 3. Update lead stage to PROPOSAL
    up_resp = await client.put(
        f"/api/v1/tenant-admin/leads/{lead_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "PROPOSAL", "priority": "HOT", "notes": "Proposal sent via email."},
    )
    assert up_resp.status_code == 200
    assert up_resp.json()["status"] == "PROPOSAL"
    assert up_resp.json()["priority"] == "HOT"


@pytest.mark.asyncio
async def test_conversations_human_takeover_and_reply(client: AsyncClient, tenant_and_user_token):
    token, tenant, user = tenant_and_user_token

    # Start simulation
    with patch("app.services.groq_client.GroqClient.chat_completion") as mock_chat:
        mock_chat.side_effect = ["Hello!", '{"intent": "general_inquiry", "score": 30, "needs_human_handoff": false}']
        sim_res = await client.post(
            "/api/v1/tenant-admin/chat/simulate",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "Can I negotiate a lower price?"},
        )
        session_id = sim_res.json()["session_id"]

    # List conversations
    convs_resp = await client.get(
        "/api/v1/tenant-admin/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert convs_resp.status_code == 200
    assert convs_resp.json()["total"] >= 1

    # Takeover conversation
    takeover_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{session_id}/takeover",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert takeover_resp.status_code == 200
    assert takeover_resp.json()["status"] == "ESCALATED_HUMAN"

    # Human agent sends reply
    reply_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{session_id}/reply",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": "Hi there, I am Sam from sales. How can I help you today?"},
    )
    assert reply_resp.status_code == 200
    assert reply_resp.json()["sender_type"] == "HUMAN_AGENT"

    # Hand back to AI
    handback_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{session_id}/handback",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert handback_resp.status_code == 200
    assert handback_resp.json()["status"] == "ACTIVE_AI"

    # Resolve conversation
    resolve_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{session_id}/resolve",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "RESOLVED"


@pytest.mark.asyncio
async def test_tenant_dashboard_kpis(client: AsyncClient, tenant_and_user_token):
    token, tenant, user = tenant_and_user_token

    dash_resp = await client.get(
        "/api/v1/tenant-admin/dashboard",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert dash_resp.status_code == 200
    data = dash_resp.json()
    assert "total_leads" in data
    assert "total_conversations" in data
    assert "total_pipeline_value" in data
    assert "total_products" in data
    assert data["total_products"] >= 1  # From fixture
