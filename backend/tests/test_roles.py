"""Team roles: roles an admin creates, the role dropdown when adding people, and who receives automatic deals."""
import uuid

import pytest
from httpx import AsyncClient

from app.services.lead_assignment_service import LeadAssignmentService
from tests.factories import make_conversation, make_customer, make_lead

ROLES = "/api/v1/tenant-admin/roles"
USERS = "/api/v1/users"
PASSWORD = "TeamPass123!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _roles(client, token) -> dict:
    return {r["name"]: r for r in (await client.get(ROLES, headers=_auth(token))).json()}


async def _role(client, token, name, **kw) -> dict:
    resp = await client.post(ROLES, json={"name": name, **kw}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _member(client, token, role_id=None, **kw) -> dict:
    body = {"first_name": "Pat", "last_name": "Person", "email": f"p_{uuid.uuid4().hex[:8]}@test.com", "password": PASSWORD}
    body.update(kw)
    if role_id:
        body["role_id"] = role_id
    resp = await client.post(USERS, json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _login(client, email) -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_every_team_starts_with_the_two_built_in_roles(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    roles = await _roles(client, token)
    assert set(roles) == {"Sales Rep", "Admin"}
    assert (roles["Sales Rep"]["access"], roles["Sales Rep"]["receives_deals"], roles["Sales Rep"]["is_system"]) == ("SALES", True, True)
    assert (roles["Admin"]["access"], roles["Admin"]["receives_deals"]) == ("ADMIN", False)
    assert roles["Admin"]["member_count"] == 1 and roles["Sales Rep"]["member_count"] == 0  # the admin themselves


@pytest.mark.asyncio
async def test_admin_creates_edits_and_deletes_a_role(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    support = await _role(client, token, "  Customer   Support ", access="SALES", receives_deals=False)
    assert support["name"] == "Customer Support" and support["receives_deals"] is False and support["is_system"] is False

    dup = await client.post(ROLES, json={"name": "customer support"}, headers=_auth(token))
    assert dup.status_code == 409
    assert (await client.post(ROLES, json={"name": "sales rep"}, headers=_auth(token))).status_code == 409  # built-in name

    manager = await _role(client, token, "Manager", access="ADMIN", receives_deals=True)
    assert manager["receives_deals"] is False  # admin-level roles never get automatic deals

    renamed = await client.put(f"{ROLES}/{support['id']}", json={"name": "Support Desk", "receives_deals": True}, headers=_auth(token))
    assert renamed.status_code == 200 and renamed.json()["name"] == "Support Desk" and renamed.json()["receives_deals"] is True
    clash = await client.put(f"{ROLES}/{support['id']}", json={"name": "Manager"}, headers=_auth(token))
    assert clash.status_code == 409
    still_admin = await client.put(f"{ROLES}/{manager['id']}", json={"receives_deals": True}, headers=_auth(token))
    assert still_admin.json()["receives_deals"] is False

    assert (await client.delete(f"{ROLES}/{support['id']}", headers=_auth(token))).status_code == 204
    assert "Support Desk" not in await _roles(client, token)


@pytest.mark.asyncio
async def test_built_in_roles_and_roles_with_people_cannot_be_removed(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    built_in = await _roles(client, token)
    for role in built_in.values():
        assert (await client.delete(f"{ROLES}/{role['id']}", headers=_auth(token))).status_code == 400
        assert (await client.put(f"{ROLES}/{role['id']}", json={"name": "Renamed"}, headers=_auth(token))).status_code == 400

    closer = await _role(client, token, "Closer")
    await _member(client, token, closer["id"])
    blocked = await client.delete(f"{ROLES}/{closer['id']}", headers=_auth(token))
    assert blocked.status_code == 409 and "1 person has this role" in blocked.json()["detail"]
    assert (await _roles(client, token))["Closer"]["member_count"] == 1


@pytest.mark.asyncio
async def test_roles_belong_to_one_tenant_and_only_admins_manage_them(client: AsyncClient, make_tenant_admin, sales_user_token):
    token_a, _, _ = await make_tenant_admin()
    token_b, _, _ = await make_tenant_admin()
    mine = await _role(client, token_a, "Only Mine")

    assert "Only Mine" not in await _roles(client, token_b)
    assert (await client.put(f"{ROLES}/{mine['id']}", json={"name": "Taken"}, headers=_auth(token_b))).status_code == 404
    assert (await client.delete(f"{ROLES}/{mine['id']}", headers=_auth(token_b))).status_code == 404
    # nor can another tenant hand its people my role
    body = {"first_name": "X", "last_name": "Y", "email": f"x_{uuid.uuid4().hex[:6]}@test.com", "password": PASSWORD, "role_id": mine["id"]}
    assert (await client.post(USERS, json=body, headers=_auth(token_b))).status_code == 422

    assert (await client.get(ROLES, headers=_auth(sales_user_token))).status_code == 403
    assert (await client.post(ROLES, json={"name": "Sneaky", "access": "ADMIN"}, headers=_auth(sales_user_token))).status_code == 403


@pytest.mark.asyncio
async def test_the_chosen_role_decides_access_and_title(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    roles = await _roles(client, token)
    manager = await _role(client, token, "Manager", access="ADMIN")
    support = await _role(client, token, "Support", access="SALES", receives_deals=False)

    rep = await _member(client, token)  # no role chosen = Sales Rep
    assert (rep["role"], rep["role_name"], rep["receives_deals"], rep["role_id"]) == ("SALES_USER", "Sales Rep", True, roles["Sales Rep"]["id"])
    helper = await _member(client, token, support["id"])
    assert (helper["role"], helper["role_name"], helper["receives_deals"]) == ("SALES_USER", "Support", False)
    boss = await _member(client, token, manager["id"])
    assert (boss["role"], boss["role_name"], boss["receives_deals"]) == ("TENANT_ADMIN", "Manager", False)

    # an admin-level role really is admin access: this person can open the Team list, a sales person cannot
    boss_token = await _login(client, boss["email"])
    assert (await client.get(USERS, headers=_auth(boss_token))).status_code == 200
    helper_token = await _login(client, helper["email"])
    assert (await client.get(USERS, headers=_auth(helper_token))).status_code == 403


@pytest.mark.asyncio
async def test_changing_to_a_role_with_other_access_signs_the_person_out(client: AsyncClient, make_tenant_admin):
    token, _, _ = await make_tenant_admin()
    roles = await _roles(client, token)
    manager = await _role(client, token, "Manager", access="ADMIN")
    boss = await _member(client, token, manager["id"])
    boss_token = await _login(client, boss["email"])
    assert (await client.get(USERS, headers=_auth(boss_token))).status_code == 200

    demoted = await client.put(f"{USERS}/{boss['id']}", json={"role_id": roles["Sales Rep"]["id"]}, headers=_auth(token))
    assert demoted.status_code == 200 and demoted.json()["role"] == "SALES_USER" and demoted.json()["role_name"] == "Sales Rep"
    assert (await client.get(f"{USERS}/me", headers=_auth(boss_token))).status_code == 401  # old session is gone
    assert (await client.get(USERS, headers=_auth(await _login(client, boss["email"])))).status_code == 403

    # a new title with the same access keeps the session
    support = await _role(client, token, "Support")
    rep = await _member(client, token)
    rep_token = await _login(client, rep["email"])
    retitled = await client.put(f"{USERS}/{rep['id']}", json={"role_id": support["id"]}, headers=_auth(token))
    assert retitled.json()["role_name"] == "Support"
    assert (await client.get(f"{USERS}/me", headers=_auth(rep_token))).status_code == 200

    assert (await client.put(f"{USERS}/{rep['id']}", json={"role_id": str(uuid.uuid4())}, headers=_auth(token))).status_code == 422
    assert (await client.put(f"{USERS}/{rep['id']}", json={"role_id": None}, headers=_auth(token))).status_code == 422


@pytest.mark.asyncio
async def test_an_admin_cannot_lock_themselves_out(client: AsyncClient, make_tenant_admin):
    token, _, admin = await make_tenant_admin()
    roles = await _roles(client, token)
    me = f"{USERS}/{admin.id}"
    assert (await client.patch(f"{me}/status", json={"is_active": False}, headers=_auth(token))).status_code == 400
    assert (await client.delete(me, headers=_auth(token))).status_code == 400
    assert (await client.post(f"{me}/reset-password", json={"new_password": "Another123!"}, headers=_auth(token))).status_code == 400
    assert (await client.put(me, json={"role_id": roles["Sales Rep"]["id"]}, headers=_auth(token))).status_code == 400

    ok = await client.put(me, json={"first_name": "Renamed", "role_id": roles["Admin"]["id"]}, headers=_auth(token))
    assert ok.status_code == 200 and ok.json()["first_name"] == "Renamed" and ok.json()["role"] == "TENANT_ADMIN"
    assert (await client.get(f"{USERS}/me", headers=_auth(token))).status_code == 200  # still signed in


@pytest.mark.asyncio
async def test_only_roles_that_receive_deals_are_in_the_round_robin(client: AsyncClient, test_db, make_tenant_admin):
    token, tenant, admin = await make_tenant_admin()
    support = await _role(client, token, "Support", receives_deals=False)
    manager = await _role(client, token, "Manager", access="ADMIN")
    await _member(client, token, support["id"], first_name="Sue")
    await _member(client, token, manager["id"], first_name="Max")

    async def lead(title):
        cust = await make_customer(test_db, tenant, title)
        return await make_lead(test_db, tenant, cust, await make_conversation(test_db, tenant, cust), title=title)

    # nobody on this team is a deal-receiving sales person: neither the admin, the manager nor support
    assert await LeadAssignmentService.assign_lead_round_robin(test_db, tenant.id, (await lead("Nobody")).id) is None

    closer = await _role(client, token, "Closer")  # a Sales-level role that does receive deals
    reps = [await _member(client, token, closer["id"], first_name=n) for n in ("Rita", "Raj")]
    picked = []
    for title in ("One", "Two", "Three", "Four"):
        rep = await LeadAssignmentService.assign_lead_round_robin(test_db, tenant.id, (await lead(title)).id)
        picked.append(str(rep.id))
    assert set(picked) == {r["id"] for r in reps} and picked[0] == picked[2] and picked[1] == picked[3]
    assert str(admin.id) not in picked
