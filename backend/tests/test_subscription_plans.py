import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription_plan import BillingCycle


@pytest.mark.asyncio
async def test_super_admin_can_create_plan(client: AsyncClient, super_admin_token: str):
    response = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "name": "Pro Plan",
            "description": "Best for professionals",
            "price": 49.99,
            "currency": "USD",
            "billing_cycle": BillingCycle.MONTHLY.value,
            "max_users": 10,
            "max_branches": 2,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Pro Plan"
    assert data["slug"] == "pro-plan"
    assert data["price"] == 49.99
    assert data["is_active"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_duplicate_plan_slug_resolution(client: AsyncClient, super_admin_token: str):
    # Create first plan
    await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Basic Plan", "price": 10.0, "max_users": 1, "max_branches": 1},
    )
    
    # Create second plan with same name
    response = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Basic Plan", "price": 15.0, "max_users": 2, "max_branches": 1},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Basic Plan"
    assert data["slug"] == "basic-plan-1"


@pytest.mark.asyncio
async def test_invalid_plan_data_rejected(client: AsyncClient, super_admin_token: str):
    response = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Free", "price": -5.0, "max_users": 0, "max_branches": 1},
    )
    # Validation errors on price and max_users
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_super_admin_can_retrieve_plan(client: AsyncClient, super_admin_token: str):
    create_resp = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Fetch Me Plan", "price": 0, "max_users": 1, "max_branches": 1},
    )
    plan_id = create_resp.json()["id"]

    get_resp = await client.get(
        f"/api/v1/super-admin/subscription-plans/{plan_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Fetch Me Plan"


@pytest.mark.asyncio
async def test_super_admin_can_update_plan(client: AsyncClient, super_admin_token: str):
    create_resp = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Update Me Plan", "price": 10, "max_users": 1, "max_branches": 1},
    )
    plan_id = create_resp.json()["id"]

    update_resp = await client.patch(
        f"/api/v1/super-admin/subscription-plans/{plan_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"price": 20, "max_users": 5},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["price"] == 20.0
    assert update_resp.json()["max_users"] == 5


@pytest.mark.asyncio
async def test_plan_status_transitions(client: AsyncClient, super_admin_token: str):
    create_resp = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Status Check Plan", "price": 10, "max_users": 1, "max_branches": 1},
    )
    plan_id = create_resp.json()["id"]

    # Deactivate
    resp1 = await client.patch(
        f"/api/v1/super-admin/subscription-plans/{plan_id}/status",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"is_active": False},
    )
    assert resp1.status_code == 200
    assert resp1.json()["is_active"] is False


@pytest.mark.asyncio
async def test_assign_plan_to_tenant(client: AsyncClient, super_admin_token: str):
    # 1. Create a tenant
    tenant_resp = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Plan Assigned Corp"},
    )
    tenant_id = tenant_resp.json()["id"]

    # 2. Create a plan
    plan_resp = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Assignment Plan", "price": 99.0, "max_users": 10, "max_branches": 5},
    )
    plan_id = plan_resp.json()["id"]

    # 3. Assign plan to tenant
    assign_resp = await client.patch(
        f"/api/v1/super-admin/tenants/{tenant_id}/subscription-plan",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"subscription_plan_id": plan_id},
    )
    assert assign_resp.status_code == 200
    
    # 4. Verify assignment
    get_tenant_resp = await client.get(
        f"/api/v1/super-admin/tenants/{tenant_id}",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert get_tenant_resp.json()["subscription_plan_id"] == plan_id


@pytest.mark.asyncio
async def test_cannot_assign_inactive_plan(client: AsyncClient, super_admin_token: str):
    # 1. Create a tenant
    tenant_resp = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Inactive Plan Test Corp"},
    )
    tenant_id = tenant_resp.json()["id"]

    # 2. Create a plan and deactivate it
    plan_resp = await client.post(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Inactive Plan", "price": 0, "max_users": 1, "max_branches": 1},
    )
    plan_id = plan_resp.json()["id"]
    await client.patch(
        f"/api/v1/super-admin/subscription-plans/{plan_id}/status",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"is_active": False},
    )

    # 3. Try to assign inactive plan
    assign_resp = await client.patch(
        f"/api/v1/super-admin/tenants/{tenant_id}/subscription-plan",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"subscription_plan_id": plan_id},
    )
    assert assign_resp.status_code == 400
    assert "inactive" in assign_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_authorization(client: AsyncClient, tenant_admin_token: str, sales_user_token: str):
    # Tenant Admin forbidden
    resp1 = await client.get(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {tenant_admin_token}"}
    )
    assert resp1.status_code == 403

    # Sales User forbidden
    resp2 = await client.get(
        "/api/v1/super-admin/subscription-plans",
        headers={"Authorization": f"Bearer {sales_user_token}"}
    )
    assert resp2.status_code == 403

    # Unauthenticated unauthorized
    resp3 = await client.get("/api/v1/super-admin/subscription-plans")
    assert resp3.status_code == 403
