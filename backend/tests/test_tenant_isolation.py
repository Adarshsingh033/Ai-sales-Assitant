import pytest
from fastapi import APIRouter, Depends
from httpx import AsyncClient

from app.core.dependencies import get_current_tenant
from app.main import app
from app.models.tenant import Tenant


# We create a dummy router to test the dependency isolation.
dummy_router = APIRouter(prefix="/test-isolation")

@dummy_router.get("/my-tenant-data")
async def get_my_data(tenant: Tenant = Depends(get_current_tenant)):
    return {"tenant_id": str(tenant.id), "message": "success"}

app.include_router(dummy_router)


@pytest.mark.asyncio
async def test_sales_user_can_access_own_tenant(client: AsyncClient, sales_user_token: str):
    response = await client.get(
        "/test-isolation/my-tenant-data",
        headers={"Authorization": f"Bearer {sales_user_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "success"
    assert "tenant_id" in response.json()


@pytest.mark.asyncio
async def test_super_admin_cannot_use_tenant_context_directly(client: AsyncClient, super_admin_token: str):
    # Super admins don't have a tenant_id, so getting "current tenant" directly fails
    response = await client.get(
        "/test-isolation/my-tenant-data",
        headers={"Authorization": f"Bearer {super_admin_token}"}
    )
    assert response.status_code == 403
    assert "does not belong to a tenant" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unauthenticated_user_cannot_access_tenant_data(client: AsyncClient):
    response = await client.get("/test-isolation/my-tenant-data")
    assert response.status_code == 403
