"""
API endpoint tests for Product Catalog, Knowledge Base, and AI Config.
"""
from unittest.mock import patch
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.core.security import create_access_token
from app.services.rag_service import RAGService


@pytest.fixture()
async def two_tenants_and_tokens(test_db: AsyncSession):
    import uuid
    uid1 = uuid.uuid4().hex[:6]
    uid2 = uuid.uuid4().hex[:6]
    # Tenant 1
    t1 = Tenant(name=f"Alpha Tech {uid1}", slug=f"alpha-tech-{uid1}")
    test_db.add(t1)
    await test_db.flush()
    u1 = User(
        first_name="Alpha", last_name="Admin", email=f"admin_{uid1}@alpha.com",
        password_hash="fake", role=UserRole.TENANT_ADMIN, tenant_id=t1.id, is_active=True
    )
    test_db.add(u1)

    # Tenant 2
    t2 = Tenant(name=f"Beta Corp {uid2}", slug=f"beta-corp-{uid2}")
    test_db.add(t2)
    await test_db.flush()
    u2 = User(
        first_name="Beta", last_name="Admin", email=f"admin_{uid2}@beta.com",
        password_hash="fake", role=UserRole.TENANT_ADMIN, tenant_id=t2.id, is_active=True
    )
    test_db.add(u2)
    await test_db.commit()

    token1 = create_access_token(str(u1.id), u1.role.value, str(t1.id))
    token2 = create_access_token(str(u2.id), u2.role.value, str(t2.id))
    return (token1, t1), (token2, t2)


@pytest.mark.asyncio
async def test_product_crud_and_isolation(client: AsyncClient, two_tenants_and_tokens):
    (token1, t1), (token2, t2) = two_tenants_and_tokens

    # Create product for Tenant 1
    resp = await client.post(
        "/api/v1/tenant-admin/products",
        headers={"Authorization": f"Bearer {token1}"},
        json={
            "name": "Cloud CRM Pro",
            "sku": "CRM-01",
            "category": "SaaS",
            "price": 49.99,
            "currency": "USD",
            "description": "All-in-one sales CRM.",
        },
    )
    assert resp.status_code == 201
    prod_data = resp.json()
    assert prod_data["name"] == "Cloud CRM Pro"
    prod_id = prod_data["id"]

    # Tenant 1 can view it
    list1 = await client.get(
        "/api/v1/tenant-admin/products",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert list1.status_code == 200
    assert list1.json()["total"] == 1

    # Tenant 2 CANNOT see Tenant 1's product
    list2 = await client.get(
        "/api/v1/tenant-admin/products",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert list2.status_code == 200
    assert list2.json()["total"] == 0

    # Tenant 1 updates product
    up_resp = await client.put(
        f"/api/v1/tenant-admin/products/{prod_id}",
        headers={"Authorization": f"Bearer {token1}"},
        json={"price": 59.99},
    )
    assert up_resp.status_code == 200
    assert up_resp.json()["price"] == 59.99

    # Tenant 1 deletes product
    del_resp = await client.delete(
        f"/api/v1/tenant-admin/products/{prod_id}",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert del_resp.status_code == 204


@pytest.mark.asyncio
async def test_ai_config_get_and_update(client: AsyncClient, two_tenants_and_tokens):
    (token1, t1), _ = two_tenants_and_tokens

    # Get default config
    resp = await client.get(
        "/api/v1/tenant-admin/ai-config",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp.status_code == 200
    config_data = resp.json()
    assert config_data["assistant_name"] == "Sales Assistant"
    assert config_data["max_discount_percent"] == 10.0
    assert config_data["has_groq_api_key"] is False

    # Update config
    update_resp = await client.put(
        "/api/v1/tenant-admin/ai-config",
        headers={"Authorization": f"Bearer {token1}"},
        json={
            "assistant_name": "Maya AI",
            "personality": "Consultative",
            "custom_instructions": "Focus on high-value B2B deals.",
            "max_discount_percent": 15.0,
            "groq_api_key": "gsk_test12345",
        },
    )
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    assert updated_data["assistant_name"] == "Maya AI"
    assert updated_data["personality"] == "Consultative"
    assert updated_data["max_discount_percent"] == 15.0
    assert updated_data["has_groq_api_key"] is True


@pytest.mark.asyncio
async def test_knowledge_upload_and_search_api(client: AsyncClient, two_tenants_and_tokens):
    (token1, t1), _ = two_tenants_and_tokens

    sample_content = b"Enterprise licenses cost $1200 per year and include dedicated account manager."

    def mock_embeddings(texts):
        return [[0.8, 0.2, 0.0] for _ in texts]

    with patch.object(RAGService, "generate_embeddings", side_effect=mock_embeddings):
        # Upload file
        upload_resp = await client.post(
            "/api/v1/tenant-admin/knowledge/upload",
            headers={"Authorization": f"Bearer {token1}"},
            files={"file": ("faq.txt", sample_content, "text/plain")},
        )
        assert upload_resp.status_code == 201
        doc_data = upload_resp.json()
        assert doc_data["filename"] == "faq.txt"
        doc_id = doc_data["id"]

        # List documents
        list_resp = await client.get(
            "/api/v1/tenant-admin/knowledge/documents",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

        # Test search
        search_resp = await client.post(
            "/api/v1/tenant-admin/knowledge/test-search",
            headers={"Authorization": f"Bearer {token1}"},
            json={"query": "How much is the enterprise license?", "top_k": 2},
        )
        assert search_resp.status_code == 200
        matches = search_resp.json()["matches"]
        assert len(matches) >= 1
        assert "Enterprise licenses" in matches[0]["content"]

        # Delete document
        del_resp = await client.delete(
            f"/api/v1/tenant-admin/knowledge/documents/{doc_id}",
            headers={"Authorization": f"Bearer {token1}"},
        )
        assert del_resp.status_code == 204
