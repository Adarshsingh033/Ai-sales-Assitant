"""
Comprehensive End-to-End Verification Test for Phase 1 MVP
Simulates the complete journey:
1. AI Assistant Configuration
2. Product Catalog Management
3. Knowledge Base Upload & Vector Search
4. Autonomous AI Chat Simulation & Lead Qualification
5. Pipeline Stage Update
6. Live Inbox, Human Takeover, Manual Reply, Handback, and Resolution
7. Tenant Analytics Dashboard Verification
"""
from io import BytesIO
from unittest.mock import patch
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.core.security import create_access_token
from app.services.rag_service import RAGService


@pytest.fixture()
async def e2e_tenant_token(test_db: AsyncSession):
    uid = uuid.uuid4().hex[:6]
    tenant = Tenant(name=f"E2E Enterprise {uid}", slug=f"e2e-enterprise-{uid}")
    test_db.add(tenant)
    await test_db.flush()

    user = User(
        first_name="Jane",
        last_name="Executive",
        email=f"jane_{uid}@enterprise.com",
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
async def test_complete_phase1_mvp_flow(client: AsyncClient, e2e_tenant_token):
    token, tenant, user = e2e_tenant_token
    auth_headers = {"Authorization": f"Bearer {token}"}

    # -------------------------------------------------------------------------
    # 1. Configure AI Assistant
    # -------------------------------------------------------------------------
    ai_config_payload = {
        "assistant_name": "Maya",
        "personality": "Consultative",
        "greeting_message": "Hello! I am Maya, your dedicated sales advisor.",
        "custom_instructions": "Always be polite, recommend Enterprise Cloud ERP, and qualify timeline.",
        "max_discount_percent": 15.0,
        "whatsapp_verify_token": "secret_wh_token_xyz",
        "whatsapp_phone_number_id": "phone_id_9999",
    }
    cfg_resp = await client.put(
        "/api/v1/tenant-admin/ai-config",
        headers=auth_headers,
        json=ai_config_payload,
    )
    assert cfg_resp.status_code == 200
    cfg_data = cfg_resp.json()
    assert cfg_data["assistant_name"] == "Maya"
    assert cfg_data["personality"] == "Consultative"
    assert cfg_data["max_discount_percent"] == 15.0

    # -------------------------------------------------------------------------
    # 2. Add Product to Catalog
    # -------------------------------------------------------------------------
    prod_payload = {
        "name": "OmniSuite Enterprise",
        "sku": "OMNI-ENT-2026",
        "category": "Software",
        "price": 12000.00,
        "currency": "USD",
        "description": "All-in-one ERP, CRM, and Billing cloud suite.",
        "features": "Multi-tenant architecture, Real-time ledger, AI sales automation",
        "is_active": True,
    }
    prod_resp = await client.post(
        "/api/v1/tenant-admin/products",
        headers=auth_headers,
        json=prod_payload,
    )
    assert prod_resp.status_code == 201
    prod_data = prod_resp.json()
    product_id = prod_data["id"]
    assert prod_data["name"] == "OmniSuite Enterprise"
    assert prod_data["price"] == 12000.00

    # -------------------------------------------------------------------------
    # 3. Upload Knowledge Document & Test Vector Retrieval
    # -------------------------------------------------------------------------
    def mock_embeddings(texts):
        return [[0.05] * 384 for _ in texts]

    with patch.object(RAGService, "generate_embeddings", side_effect=mock_embeddings):
        doc_content = b"OmniSuite includes a 30-day money-back satisfaction guarantee and 99.99% uptime SLA."
        upload_resp = await client.post(
            "/api/v1/tenant-admin/knowledge/upload",
            headers=auth_headers,
            files={"file": ("sla_policy.txt", BytesIO(doc_content), "text/plain")},
        )
        assert upload_resp.status_code == 201
        doc_data = upload_resp.json()
        assert doc_data["filename"] == "sla_policy.txt"
        assert doc_data["chunk_count"] >= 1

        # Test Semantic Search
        search_resp = await client.post(
            "/api/v1/tenant-admin/knowledge/test-search",
            headers=auth_headers,
            json={"query": "What is the uptime SLA or money-back guarantee?", "top_k": 3},
        )
        assert search_resp.status_code == 200
        search_results = search_resp.json()["matches"]
        assert len(search_results) >= 1
        assert "money-back" in search_results[0]["content"]

    # -------------------------------------------------------------------------
    # 4. Simulate Buyer Interaction & Autonomous Lead Qualification
    # -------------------------------------------------------------------------
    sim_groq_resp = "OmniSuite Enterprise is $12,000/year and comes with a 30-day money-back guarantee and 99.99% uptime SLA."
    sim_groq_extraction = (
        '{"intent": "purchase_intent", "buying_intent": true, "customer_name": "Jordan Smith", '
        '"company": "Apex Dynamics", "budget": 12000, "timeline": "Q1 2026", '
        '"requirements": "OmniSuite ERP with uptime SLA", "score": 92, "qualification_score": 92, '
        '"score_reason": "High intent buyer with verified budget and urgent timeline", '
        '"priority": "HOT", "recommended_next_step": "Send proposal and schedule executive demo", '
        '"needs_human_handoff": false}'
    )

    with patch("app.services.groq_client.GroqClient.chat_completion") as mock_groq:
        mock_groq.side_effect = [sim_groq_resp, sim_groq_extraction]

        sim_post_resp = await client.post(
            "/api/v1/tenant-admin/chat/simulate",
            headers=auth_headers,
            json={
                "customer_phone": "+15559876543",
                "customer_name": "Jordan Smith",
                "message": "We need OmniSuite ERP for 50 users by next month. Budget is $12k.",
            },
        )
        assert sim_post_resp.status_code == 200
        sim_data = sim_post_resp.json()
        assert "OmniSuite" in sim_data["reply"]
        assert sim_data["score"] == 92
        conversation_id = sim_data["session_id"]

    # -------------------------------------------------------------------------
    # 5. Verify Lead Pipeline Auto-Sync & Advance Stage
    # -------------------------------------------------------------------------
    leads_resp = await client.get("/api/v1/tenant-admin/leads", headers=auth_headers)
    assert leads_resp.status_code == 200
    leads_list = leads_resp.json()["items"]
    assert len(leads_list) >= 1
    lead = leads_list[0]
    lead_id = lead["id"]
    assert lead["score"] == 92
    assert lead["priority"] == "HOT"
    assert lead["budget"] == 12000.0

    # Advance stage to QUALIFIED and add notes
    update_lead_resp = await client.put(
        f"/api/v1/tenant-admin/leads/{lead_id}",
        headers=auth_headers,
        json={"status": "QUALIFIED", "priority": "HOT", "notes": "Approved by lead executive."},
    )
    assert update_lead_resp.status_code == 200
    assert update_lead_resp.json()["status"] == "QUALIFIED"

    # -------------------------------------------------------------------------
    # 6. Live Conversations: Inspect Thread, Take Over, Human Reply, Handback, Resolve
    # -------------------------------------------------------------------------
    # Retrieve conversation
    conv_resp = await client.get(
        f"/api/v1/tenant-admin/conversations/{conversation_id}",
        headers=auth_headers,
    )
    assert conv_resp.status_code == 200
    assert conv_resp.json()["status"] == "ACTIVE_AI"

    # Retrieve message thread
    msgs_resp = await client.get(
        f"/api/v1/tenant-admin/conversations/{conversation_id}/messages",
        headers=auth_headers,
    )
    assert msgs_resp.status_code == 200
    msgs = msgs_resp.json()
    assert len(msgs) >= 2  # Customer message + AI response

    # Human Takeover
    takeover_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{conversation_id}/takeover",
        headers=auth_headers,
    )
    assert takeover_resp.status_code == 200
    assert takeover_resp.json()["status"] == "ESCALATED_HUMAN"

    # Send Human Agent Reply
    reply_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{conversation_id}/reply",
        headers=auth_headers,
        json={"content": "Hi Jordan, Jane here from Enterprise sales. I have reserved your deployment slot!"},
    )
    assert reply_resp.status_code == 200
    assert reply_resp.json()["sender_type"] == "HUMAN_AGENT"

    # Handback to AI Assistant
    handback_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{conversation_id}/handback",
        headers=auth_headers,
    )
    assert handback_resp.status_code == 200
    assert handback_resp.json()["status"] == "ACTIVE_AI"

    # Resolve Conversation
    resolve_resp = await client.post(
        f"/api/v1/tenant-admin/conversations/{conversation_id}/resolve",
        headers=auth_headers,
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "RESOLVED"

    # -------------------------------------------------------------------------
    # 7. Verify Dashboard Analytics KPIs
    # -------------------------------------------------------------------------
    dash_resp = await client.get(
        "/api/v1/tenant-admin/dashboard",
        headers=auth_headers,
    )
    assert dash_resp.status_code == 200
    dash_data = dash_resp.json()
    assert dash_data["total_leads"] >= 1
    assert dash_data["hot_leads"] >= 1
    assert dash_data["total_pipeline_value"] >= 12000.0
    assert dash_data["total_products"] >= 1
    assert len(dash_data["recent_leads"]) >= 1
    assert len(dash_data["recent_conversations"]) >= 1
