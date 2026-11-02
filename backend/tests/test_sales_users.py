"""
Sales User Management tests — Tenant Admin managing Sales Users in their tenant.

Covers CRUD, global email uniqueness, subscription user limits, tenant
isolation, RBAC, session revocation on deactivation / password reset,
and list search / filter / sort / pagination.
"""
import uuid

import pytest
from httpx import AsyncClient

BASE = "/api/v1/users"
PASSWORD = "SalesPass123!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides) -> dict:
    data = {
        "first_name": "Sam",
        "last_name": "Seller",
        "email": f"sales_{uuid.uuid4().hex[:8]}@test.com",
        "phone_number": "+15550001111",
        "password": PASSWORD,
        "is_available": True,
    }
    data.update(overrides)
    return data


async def _create(client: AsyncClient, token: str, **overrides) -> dict:
    resp = await client.post(BASE, json=_payload(**overrides), headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client: AsyncClient, email: str, password: str = PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_crud_happy_path(client: AsyncClient, make_tenant_admin):
    token, tenant, admin = await make_tenant_admin(max_users=5)

    created = await _create(client, token, email="Crud.User@Test.com")
    assert created["role_name"] == "Sales Rep" and created["receives_deals"] is True and created["is_me"] is False
    assert created["role"] == "SALES_USER"
    assert created["tenant_id"] == str(tenant.id)
    assert created["email"] == "crud.user@test.com"
    assert created["is_active"] is True
    assert created["is_available"] is True
    assert "password_hash" not in created and "token_version" not in created

    user_id = created["id"]

    resp = await client.get(f"{BASE}/{user_id}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["email"] == "crud.user@test.com"

    resp = await client.put(
        f"{BASE}/{user_id}",
        json={"first_name": "Samuel", "is_available": False},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["first_name"] == "Samuel"
    assert resp.json()["is_available"] is False

    resp = await client.get(BASE, headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2  # the team: this admin and the new sales person
    assert {i["id"] for i in body["items"]} == {user_id, str(admin.id)}

    resp = await client.delete(f"{BASE}/{user_id}", headers=_auth(token))
    assert resp.status_code == 204

    resp = await client.get(f"{BASE}/{user_id}", headers=_auth(token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_validation_errors(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    resp = await client.post(BASE, json=_payload(password="short"), headers=_auth(token))
    assert resp.status_code == 422
    resp = await client.post(BASE, json=_payload(email="not-an-email"), headers=_auth(token))
    assert resp.status_code == 422
    resp = await client.post(BASE, json=_payload(first_name="   "), headers=_auth(token))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_ignores_email_and_role(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    created = await _create(client, token)
    resp = await client.put(
        f"{BASE}/{created['id']}",
        json={"email": "hijack@test.com", "role": "TENANT_ADMIN", "tenant_id": str(uuid.uuid4())},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == created["email"]
    assert resp.json()["role"] == "SALES_USER"
    assert resp.json()["tenant_id"] == created["tenant_id"]


# ---------------------------------------------------------------------------
# Email uniqueness
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_email_same_tenant(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    email = f"dup_{uuid.uuid4().hex[:8]}@test.com"
    await _create(client, token, email=email)

    resp = await client.post(BASE, json=_payload(email=email.upper()), headers=_auth(token))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Email already exists."


@pytest.mark.asyncio
async def test_duplicate_email_other_tenant(client: AsyncClient, make_tenant_admin):
    token_a, _, _ = await make_tenant_admin()
    token_b, _, admin_b = await make_tenant_admin()

    email = f"cross_{uuid.uuid4().hex[:8]}@test.com"
    await _create(client, token_a, email=email)

    resp = await client.post(BASE, json=_payload(email=email), headers=_auth(token_b))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Email already exists."

    # Also collides with an existing Tenant Admin email
    resp = await client.post(BASE, json=_payload(email=admin_b.email), headers=_auth(token_a))
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Plans never restrict adding users (single free tier for now)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_plan_does_not_block_creation(client: AsyncClient, make_tenant_admin):
    """Regression: an organization with no plan assigned could not add users at all."""
    token, _, _ = await make_tenant_admin(max_users=None)
    resp = await client.post(BASE, json=_payload(), headers=_auth(token))
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_plan_user_limit_and_inactive_plan_are_not_enforced(client: AsyncClient, make_tenant_admin):
    capped, _, _ = await make_tenant_admin(max_users=1)  # a plan that would only fit the admin
    for _ in range(3):
        await _create(client, capped)
    inactive, _, _ = await make_tenant_admin(max_users=10, plan_active=False)
    assert (await client.post(BASE, json=_payload(), headers=_auth(inactive))).status_code == 201


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_tenant_access_returns_404(client: AsyncClient, make_tenant_admin):
    token_a, _, _ = await make_tenant_admin()
    token_b, _, _ = await make_tenant_admin()
    victim = await _create(client, token_a)
    vid = victim["id"]

    assert (await client.get(f"{BASE}/{vid}", headers=_auth(token_b))).status_code == 404
    assert (await client.put(f"{BASE}/{vid}", json={"first_name": "X"}, headers=_auth(token_b))).status_code == 404
    assert (await client.patch(f"{BASE}/{vid}/status", json={"is_active": False}, headers=_auth(token_b))).status_code == 404
    assert (await client.post(f"{BASE}/{vid}/reset-password", json={"new_password": "NewPass123!"}, headers=_auth(token_b))).status_code == 404
    assert (await client.delete(f"{BASE}/{vid}", headers=_auth(token_b))).status_code == 404

    listing = (await client.get(BASE, headers=_auth(token_b))).json()
    assert all(item["id"] != vid for item in listing["items"])

    # Victim untouched
    resp = await client.get(f"{BASE}/{vid}", headers=_auth(token_a))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_tenant_admin_cannot_manage_other_tenant_admins(client: AsyncClient, make_tenant_admin):
    token_a, _, admin_a = await make_tenant_admin()
    token_b, _, _ = await make_tenant_admin()
    assert (await client.get(f"{BASE}/{admin_a.id}", headers=_auth(token_b))).status_code == 404
    assert (await client.patch(f"{BASE}/{admin_a.id}/status", json={"is_active": False}, headers=_auth(token_b))).status_code == 404
    mine = await client.get(f"{BASE}/{admin_a.id}", headers=_auth(token_a))  # an admin can see their own record
    assert mine.status_code == 200 and mine.json()["is_me"] is True and mine.json()["role_name"] == "Admin"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_super_admin_forbidden(client: AsyncClient, super_admin_token: str):
    resp = await client.get(BASE, headers=_auth(super_admin_token))
    assert resp.status_code == 403
    resp = await client.post(BASE, json=_payload(), headers=_auth(super_admin_token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sales_user_forbidden(client: AsyncClient, sales_user_token: str):
    resp = await client.get(BASE, headers=_auth(sales_user_token))
    assert resp.status_code == 403
    resp = await client.post(BASE, json=_payload(), headers=_auth(sales_user_token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_forbidden(client: AsyncClient):
    resp = await client.get(BASE)
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_me_route_not_shadowed(client: AsyncClient, make_tenant_admin):
    token, _, admin = await make_tenant_admin()
    resp = await client.get(f"{BASE}/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == str(admin.id)


# ---------------------------------------------------------------------------
# Deactivation & session revocation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deactivation_revokes_sessions(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    created = await _create(client, token)

    login = await _login(client, created["email"])
    assert login.status_code == 200
    sales_token = login.json()["access_token"]
    assert (await client.get(f"{BASE}/me", headers=_auth(sales_token))).status_code == 200

    resp = await client.patch(f"{BASE}/{created['id']}/status", json={"is_active": False}, headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_active"] is False
    assert body["is_available"] is False
    assert body["has_open_leads"] is False
    assert body["open_leads_count"] == 0

    assert (await client.get(f"{BASE}/me", headers=_auth(sales_token))).status_code == 401
    assert (await _login(client, created["email"])).status_code == 403

    # Cannot mark an inactive user available
    resp = await client.put(f"{BASE}/{created['id']}", json={"is_available": True}, headers=_auth(token))
    assert resp.status_code == 400

    # Reactivate: login works again, availability stays false until set
    resp = await client.patch(f"{BASE}/{created['id']}/status", json={"is_active": True}, headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True
    assert resp.json()["is_available"] is False
    assert (await _login(client, created["email"])).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_revokes_sessions(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    created = await _create(client, token)
    sales_token = (await _login(client, created["email"])).json()["access_token"]

    resp = await client.post(
        f"{BASE}/{created['id']}/reset-password",
        json={"new_password": "BrandNew123!"},
        headers=_auth(token),
    )
    assert resp.status_code == 200

    assert (await client.get(f"{BASE}/me", headers=_auth(sales_token))).status_code == 401
    assert (await _login(client, created["email"])).status_code == 401
    assert (await _login(client, created["email"], "BrandNew123!")).status_code == 200


@pytest.mark.asyncio
async def test_password_reset_validation(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    created = await _create(client, token)
    resp = await client.post(
        f"{BASE}/{created['id']}/reset-password", json={"new_password": "short"}, headers=_auth(token)
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Listing: search, filter, sort, pagination
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_search_filter_sort_paginate(client: AsyncClient, make_tenant_admin):
    token, _, admin = await make_tenant_admin(max_users=10)
    admin_id = str(admin.id)  # the admin is part of the team list too; the assertions below look at the sales people
    tag = uuid.uuid4().hex[:6]
    alice = await _create(client, token, first_name="Alice", last_name="Anders", email=f"alice_{tag}@test.com")
    bob = await _create(client, token, first_name="Bob", last_name="Brown", email=f"bob_{tag}@test.com", is_available=False)
    carol = await _create(client, token, first_name="Carol", last_name="Clark", email=f"carol_{tag}@test.com")
    await client.patch(f"{BASE}/{carol['id']}/status", json={"is_active": False}, headers=_auth(token))

    # Search by first name, full name and email
    r = (await client.get(BASE, params={"search": "alice"}, headers=_auth(token))).json()
    assert [i["id"] for i in r["items"]] == [alice["id"]]
    r = (await client.get(BASE, params={"search": "Bob Brown"}, headers=_auth(token))).json()
    assert [i["id"] for i in r["items"]] == [bob["id"]]
    r = (await client.get(BASE, params={"search": f"carol_{tag}"}, headers=_auth(token))).json()
    assert [i["id"] for i in r["items"]] == [carol["id"]]

    # Filters
    r = (await client.get(BASE, params={"is_active": "false"}, headers=_auth(token))).json()
    assert [i["id"] for i in r["items"]] == [carol["id"]]
    r = (await client.get(BASE, params={"is_available": "true"}, headers=_auth(token))).json()
    assert {i["id"] for i in r["items"]} == {alice["id"], admin_id}

    # Sort (the admin, "Tenant", sorts between Carol and the rest)
    r = (await client.get(BASE, params={"sort_by": "first_name", "sort_order": "asc"}, headers=_auth(token))).json()
    assert [i["first_name"] for i in r["items"]] == ["Alice", "Bob", "Carol", "Tenant"]
    r = (await client.get(BASE, params={"sort_by": "first_name", "sort_order": "desc"}, headers=_auth(token))).json()
    assert [i["first_name"] for i in r["items"]] == ["Tenant", "Carol", "Bob", "Alice"]

    # Pagination
    r = (await client.get(BASE, params={"page": 1, "page_size": 2, "sort_by": "first_name", "sort_order": "asc"}, headers=_auth(token))).json()
    assert r["total"] == 4 and r["total_pages"] == 2 and len(r["items"]) == 2
    r = (await client.get(BASE, params={"page": 2, "page_size": 2, "sort_by": "first_name", "sort_order": "asc"}, headers=_auth(token))).json()
    assert [i["first_name"] for i in r["items"]] == ["Carol", "Tenant"]

    # The team is this tenant's sales people and admins; never the platform's super admin
    r = (await client.get(BASE, headers=_auth(token))).json()
    assert {i["role"] for i in r["items"]} == {"SALES_USER", "TENANT_ADMIN"}


@pytest.mark.asyncio
async def test_list_rejects_invalid_sort(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    resp = await client.get(BASE, params={"sort_by": "password_hash"}, headers=_auth(token))
    assert resp.status_code == 422
    resp = await client.get(BASE, params={"sort_order": "sideways"}, headers=_auth(token))
    assert resp.status_code == 422
    resp = await client.get(BASE, params={"page_size": 1000}, headers=_auth(token))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_logs_written(client: AsyncClient, make_tenant_admin, test_db):
    from sqlalchemy import select
    from app.models.audit_log import AuditLog, AuditLogAction

    token, tenant, _ = await make_tenant_admin()
    created = await _create(client, token)
    uid = created["id"]
    await client.patch(f"{BASE}/{uid}/status", json={"is_active": False}, headers=_auth(token))
    await client.post(f"{BASE}/{uid}/reset-password", json={"new_password": "Another123!"}, headers=_auth(token))
    await client.delete(f"{BASE}/{uid}", headers=_auth(token))

    rows = (await test_db.execute(
        select(AuditLog.action, AuditLog.new_values).where(AuditLog.resource_id == uid, AuditLog.tenant_id == tenant.id)
    )).all()
    actions = {r.action for r in rows}
    assert {
        AuditLogAction.USER_CREATED,
        AuditLogAction.USER_DEACTIVATED,
        AuditLogAction.USER_PASSWORD_RESET,
        AuditLogAction.USER_DELETED,
    } <= actions
    for r in rows:
        if r.new_values:
            assert "password" not in r.new_values
