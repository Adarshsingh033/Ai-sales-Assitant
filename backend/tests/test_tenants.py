import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import TenantStatus


@pytest.mark.asyncio
async def test_super_admin_can_create_tenant(client: AsyncClient, super_admin_token: str):
    response = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "name": "Test Company",
            "legal_name": "Test Company LLC",
            "email": "admin@testcompany.com",
            "industry": "Software"
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Company"
    assert data["slug"] == "test-company"
    assert data["status"] == TenantStatus.ACTIVE.value
    assert "id" in data


@pytest.mark.asyncio
async def test_duplicate_tenant_slug_resolution(client: AsyncClient, super_admin_token: str):
    # Create first tenant
    await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Alpha Corp"},
    )
    
    # Create second tenant with same name
    response = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Alpha Corp"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Alpha Corp"
    assert data["slug"] == "alpha-corp-1"


@pytest.mark.asyncio
async def test_invalid_data_rejected(client: AsyncClient, super_admin_token: str):
    response = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": ""}, # Invalid name length
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_super_admin_can_retrieve_tenant(client: AsyncClient, super_admin_token: str):
    # Create
    create_resp = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Fetch Me"},
    )
    tenant_id = create_resp.json()["id"]

    # Retrieve
    get_resp = await client.get(
        f"/api/v1/super-admin/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Fetch Me"


@pytest.mark.asyncio
async def test_non_existing_tenant_returns_404(client: AsyncClient, super_admin_token: str):
    import uuid
    fake_id = str(uuid.uuid4())
    response = await client.get(
        f"/api/v1/super-admin/tenants/{fake_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_super_admin_can_update_tenant(client: AsyncClient, super_admin_token: str):
    # Create
    create_resp = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Update Me"},
    )
    tenant_id = create_resp.json()["id"]

    # Update
    update_resp = await client.patch(
        f"/api/v1/super-admin/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"industry": "Finance"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["industry"] == "Finance"
    assert update_resp.json()["name"] == "Update Me" # Unchanged


@pytest.mark.asyncio
async def test_tenant_status_transitions(client: AsyncClient, super_admin_token: str):
    # Create
    create_resp = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Status Check"},
    )
    tenant_id = create_resp.json()["id"]

    # ACTIVE -> INACTIVE
    resp1 = await client.patch(
        f"/api/v1/super-admin/tenants/{tenant_id}/status",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"status": "INACTIVE"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "INACTIVE"

    # INACTIVE -> SUSPENDED
    resp2 = await client.patch(
        f"/api/v1/super-admin/tenants/{tenant_id}/status",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"status": "SUSPENDED"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_authorization(client: AsyncClient, tenant_admin_token: str, sales_user_token: str):
    # Tenant Admin forbidden
    resp1 = await client.get(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {tenant_admin_token}"}
    )
    assert resp1.status_code == 403

    # Sales User forbidden
    resp2 = await client.get(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {sales_user_token}"}
    )
    assert resp2.status_code == 403

    # Unauthenticated unauthorized
    resp3 = await client.get("/api/v1/super-admin/tenants")
    assert resp3.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_can_delete_tenant(client: AsyncClient, super_admin_token: str):
    # Create
    create_resp = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Delete Target"},
    )
    tenant_id = create_resp.json()["id"]

    # Delete
    del_resp = await client.delete(
        f"/api/v1/super-admin/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert del_resp.status_code == 204

    # Verify 404 on get
    get_resp = await client.get(
        f"/api/v1/super-admin/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_resp.status_code == 404

