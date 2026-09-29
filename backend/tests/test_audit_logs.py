import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.audit_log import AuditLog, AuditLogAction


@pytest.mark.asyncio
async def test_audit_log_created_on_tenant_creation(client: AsyncClient, super_admin_token: str, test_db: AsyncSession):
    # Create tenant
    create_resp = await client.post(
        "/api/v1/super-admin/tenants",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"name": "Audit Test Tenant"},
    )
    assert create_resp.status_code == 200
    tenant_id = create_resp.json()["id"]

    # Verify audit log via API
    logs_resp = await client.get(
        "/api/v1/super-admin/audit-logs",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        params={"resource_id": tenant_id, "action": AuditLogAction.TENANT_CREATED.value}
    )
    assert logs_resp.status_code == 200
    data = logs_resp.json()
    assert data["total"] >= 1
    log = data["items"][0]
    
    assert log["action"] == AuditLogAction.TENANT_CREATED.value
    assert log["resource_type"] == "TENANT"
    assert log["resource_id"] == tenant_id
    assert "Audit Test Tenant" in log["new_values"]["name"]


@pytest.mark.asyncio
async def test_authorization_audit_logs(client: AsyncClient, tenant_admin_token: str, sales_user_token: str):
    # Tenant Admin forbidden
    resp1 = await client.get(
        "/api/v1/super-admin/audit-logs",
        headers={"Authorization": f"Bearer {tenant_admin_token}"}
    )
    assert resp1.status_code == 403

    # Sales User forbidden
    resp2 = await client.get(
        "/api/v1/super-admin/audit-logs",
        headers={"Authorization": f"Bearer {sales_user_token}"}
    )
    assert resp2.status_code == 403

    # Unauthenticated unauthorized
    resp3 = await client.get("/api/v1/super-admin/audit-logs")
    assert resp3.status_code == 403


@pytest.mark.asyncio
async def test_failed_login_creates_audit_and_hides_password(client: AsyncClient, super_admin_token: str, test_db: AsyncSession):
    # Attempt bad login
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "super_fake@test.com", "password": "SuperSecretPassword123!"}
    )
    assert resp.status_code == 401
    
    # Check logs for this action
    logs_resp = await client.get(
        "/api/v1/super-admin/audit-logs",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        params={"action": AuditLogAction.LOGIN_FAILED.value}
    )
    assert logs_resp.status_code == 200
    items = logs_resp.json()["items"]
    
    # Since other tests might have run, just check if ANY log has the password
    for item in items:
        # Check description
        assert "SuperSecretPassword123!" not in item.get("description", "")
        # Check new_values if present
        if item.get("new_values"):
            assert "SuperSecretPassword123!" not in str(item["new_values"])
        
    # We should have at least one failed login
    assert len(items) >= 1
