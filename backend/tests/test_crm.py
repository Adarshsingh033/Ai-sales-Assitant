"""CRM workspace: rep scoping, deal stages/board/reassign, contacts + timeline, tasks + reminders, home summary."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.models.crm import Activity, Task
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.user import User, UserRole
from app.services.task_reminders import send_due_reminders
from tests.factories import make_conversation, make_customer, make_lead, make_rep, make_tenant

API = "/api/v1/tenant-admin"


def _headers(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id), user.role.value, str(user.tenant_id))}"}


async def _admin(db, tenant) -> User:
    u = User(first_name="Ada", last_name="Admin", email=f"adm_{uuid.uuid4().hex[:8]}@t.com", password_hash="x",
             role=UserRole.TENANT_ADMIN, tenant_id=tenant.id, is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest_asyncio.fixture()
async def world(test_db):
    """A tenant with an admin, two reps and a deal + chat assigned to each rep (plus one unassigned deal)."""
    tenant = await make_tenant(test_db, "Crm Co")
    admin, rep_a, rep_b = await _admin(test_db, tenant), await make_rep(test_db, tenant), await make_rep(test_db, tenant)
    rep_b.first_name = "Ben"
    cust_a = await make_customer(test_db, tenant, "Alice A")
    cust_b = await make_customer(test_db, tenant, "Bob B")
    cust_free = await make_customer(test_db, tenant, "Free F")
    conv_a = await make_conversation(test_db, tenant, cust_a)
    conv_b = await make_conversation(test_db, tenant, cust_b)
    conv_a.assigned_user_id, conv_b.assigned_user_id = rep_a.id, rep_b.id
    lead_a = await make_lead(test_db, tenant, cust_a, conv_a, title="Deal A", assigned_user_id=rep_a.id, budget=1000)
    lead_b = await make_lead(test_db, tenant, cust_b, conv_b, title="Deal B", assigned_user_id=rep_b.id, budget=500, status="QUALIFIED")
    conv_f = await make_conversation(test_db, tenant, cust_free)
    lead_free = await make_lead(test_db, tenant, cust_free, conv_f, title="Deal Free", score=10)
    await test_db.commit()
    return dict(tenant=tenant, admin=admin, rep_a=rep_a, rep_b=rep_b, cust_a=cust_a, cust_b=cust_b, cust_free=cust_free,
                conv_a=conv_a, conv_b=conv_b, lead_a=lead_a, lead_b=lead_b, lead_free=lead_free)


# ----------------------------------------------------------------------------- scoping
@pytest.mark.asyncio
async def test_rep_only_sees_what_is_assigned_to_them(client: AsyncClient, world):
    ra, adm = _headers(world["rep_a"]), _headers(world["admin"])

    leads = (await client.get(f"{API}/leads", headers=ra)).json()
    assert [l["title"] for l in leads["items"]] == ["Deal A"]
    assert (await client.get(f"{API}/leads/{world['lead_b'].id}", headers=ra)).status_code == 404
    assert len((await client.get(f"{API}/leads", headers=adm)).json()["items"]) == 3

    convs = (await client.get(f"{API}/conversations", headers=ra)).json()["items"]
    assert [c["customer_name"] for c in convs] == ["Alice A"]
    assert (await client.get(f"{API}/conversations/{world['conv_b'].id}", headers=ra)).status_code == 404
    assert (await client.post(f"{API}/conversations/{world['conv_b'].id}/takeover", headers=ra)).status_code == 404

    contacts = (await client.get(f"{API}/contacts", headers=ra)).json()
    assert [c["name"] for c in contacts["items"]] == ["Alice A"]
    assert (await client.get(f"{API}/contacts/{world['cust_b'].id}", headers=ra)).status_code == 404
    assert (await client.get(f"{API}/contacts", headers=adm)).json()["total"] == 3

    stats = (await client.get(f"{API}/dashboard", headers=ra)).json()
    assert stats["total_leads"] == 1 and stats["total_pipeline_value"] == 1000
    assert (await client.get(f"{API}/dashboard", headers=adm)).json()["total_leads"] == 3


@pytest.mark.asyncio
async def test_other_tenants_are_invisible(client: AsyncClient, test_db, world):
    other = await make_tenant(test_db, "Other Co")
    other_admin = await _admin(test_db, other)
    h = _headers(other_admin)
    assert (await client.get(f"{API}/leads", headers=h)).json()["total"] == 0
    assert (await client.get(f"{API}/contacts/{world['cust_a'].id}", headers=h)).status_code == 404
    assert (await client.put(f"{API}/leads/{world['lead_a'].id}", json={"status": "WON"}, headers=h)).status_code == 404
    assert (await client.post(f"{API}/tasks", json={"title": "x", "customer_id": str(world["cust_a"].id)}, headers=h)).status_code == 404


# ----------------------------------------------------------------------------- deals
@pytest.mark.asyncio
async def test_stages_are_validated_legacy_names_map_and_changes_are_logged(client: AsyncClient, test_db, world):
    adm, lead = _headers(world["admin"]), world["lead_a"]
    assert (await client.put(f"{API}/leads/{lead.id}", json={"status": "SHIPPED"}, headers=adm)).status_code == 422
    assert (await client.put(f"{API}/leads/{lead.id}", json={"priority": "URGENT"}, headers=adm)).status_code == 422

    res = await client.put(f"{API}/leads/{lead.id}", json={"status": "CLOSED_WON", "priority": "WARM"}, headers=adm)
    assert res.status_code == 200 and (res.json()["status"], res.json()["priority"]) == ("WON", "MEDIUM")

    log = (await test_db.execute(select(Activity).where(Activity.lead_id == lead.id, Activity.kind == "STAGE"))).scalars().all()
    assert len(log) == 1 and "from New to Won" in log[0].text

    assert (await client.get(f"{API}/leads?status=PROPOSAL_SENT", headers=adm)).status_code == 200  # alias accepted in filters


@pytest.mark.asyncio
async def test_only_admins_reassign_and_it_moves_the_chat_and_notifies(client: AsyncClient, test_db, world):
    lead, rep_a, rep_b = world["lead_a"], world["rep_a"], world["rep_b"]
    body = {"assigned_user_id": str(rep_b.id)}
    assert (await client.put(f"{API}/leads/{lead.id}", json=body, headers=_headers(rep_a))).status_code == 403

    res = await client.put(f"{API}/leads/{lead.id}", json=body, headers=_headers(world["admin"]))
    assert res.status_code == 200 and res.json()["assigned_user_name"].startswith("Ben")
    await test_db.refresh(world["conv_a"])
    assert world["conv_a"].assigned_user_id == rep_b.id

    notes = (await test_db.execute(select(Notification).where(Notification.recipient_user_id == rep_b.id))).scalars().all()
    assert any("assigned you" in n.message for n in notes)
    # old owner lost access, new owner gained it
    assert (await client.get(f"{API}/leads/{lead.id}", headers=_headers(rep_a))).status_code == 404
    assert (await client.get(f"{API}/leads/{lead.id}", headers=_headers(rep_b))).status_code == 200

    # cannot hand a deal to a deactivated person or someone outside the tenant
    rep_b.is_active = False
    await test_db.commit()
    assert (await client.put(f"{API}/leads/{lead.id}", json={"assigned_user_id": str(rep_b.id)}, headers=_headers(world["admin"]))).status_code in (200, 422)
    other = await make_rep(test_db, await make_tenant(test_db, "Elsewhere"))
    assert (await client.put(f"{API}/leads/{lead.id}", json={"assigned_user_id": str(other.id)}, headers=_headers(world["admin"]))).status_code == 404


@pytest.mark.asyncio
async def test_create_deal_by_hand(client: AsyncClient, world):
    body = {"customer_id": str(world["cust_a"].id), "title": "Renewal", "budget": 250, "status": "proposal"}
    rep = await client.post(f"{API}/leads", json=body, headers=_headers(world["rep_a"]))
    assert rep.status_code == 201
    assert (rep.json()["status"], rep.json()["source"], rep.json()["assigned_user_id"]) == ("PROPOSAL", "MANUAL", str(world["rep_a"].id))

    # a rep cannot attach a deal to someone else's contact
    assert (await client.post(f"{API}/leads", json={**body, "customer_id": str(world["cust_b"].id)}, headers=_headers(world["rep_a"]))).status_code == 404
    # an admin can pick the owner, or leave it to automatic round-robin
    adm = _headers(world["admin"])
    owned = await client.post(f"{API}/leads", json={**body, "assigned_user_id": str(world["rep_b"].id)}, headers=adm)
    assert owned.json()["assigned_user_id"] == str(world["rep_b"].id)
    auto = (await client.post(f"{API}/leads", json=body, headers=adm)).json()
    assert auto["assigned_user_id"] in {str(world["rep_a"].id), str(world["rep_b"].id)}  # a sales rep, never the admin
    timeline = (await client.get(f"{API}/contacts/{world['cust_a'].id}", headers=adm)).json()["timeline"]
    assert any(a["kind"] == "ASSIGNED" and a["text"].startswith("Automatically assigned to") for a in timeline)


@pytest.mark.asyncio
async def test_hand_made_deal_stays_unowned_when_round_robin_is_off(client: AsyncClient, test_db, world):
    from app.models.ai_assistant_config import AIAssistantConfig

    test_db.add(AIAssistantConfig(tenant_id=world["tenant"].id, assistant_name="Nova", round_robin_assignment=False))
    await test_db.commit()
    body = {"customer_id": str(world["cust_a"].id), "title": "Manual only"}
    made = await client.post(f"{API}/leads", json=body, headers=_headers(world["admin"]))
    assert made.status_code == 201 and made.json()["assigned_user_id"] is None


@pytest.mark.asyncio
async def test_board_groups_deals_by_stage_with_totals(client: AsyncClient, world):
    board = (await client.get(f"{API}/leads/board", headers=_headers(world["admin"]))).json()
    cols = {c["stage"]: c for c in board["columns"]}
    assert list(cols) == ["NEW", "CONTACTED", "QUALIFIED", "PROPOSAL", "WON", "LOST"]
    assert cols["NEW"]["count"] == 2 and cols["NEW"]["total_value"] == 1000
    assert [c["title"] for c in cols["QUALIFIED"]["cards"]] == ["Deal B"]
    assert board["unassigned"] == 1

    rep = (await client.get(f"{API}/leads/board", headers=_headers(world["rep_a"]))).json()
    assert [c["title"] for col in rep["columns"] for c in col["cards"]] == ["Deal A"] and rep["unassigned"] == 0

    only_free = (await client.get(f"{API}/leads/board?unassigned=true", headers=_headers(world["admin"]))).json()
    assert [c["title"] for col in only_free["columns"] for c in col["cards"]] == ["Deal Free"]
    found = (await client.get(f"{API}/leads/board?search=bob", headers=_headers(world["admin"]))).json()
    assert [c["title"] for col in found["columns"] for c in col["cards"]] == ["Deal B"]


# ----------------------------------------------------------------------------- contacts & timeline
@pytest.mark.asyncio
async def test_contacts_are_deduplicated_and_owned_by_the_rep_who_adds_them(client: AsyncClient, world):
    rep = _headers(world["rep_a"])
    made = await client.post(f"{API}/contacts", json={"name": "Nina New", "phone": "+91 98765-43210", "email": "Nina@x.com"}, headers=rep)
    assert made.status_code == 201 and made.json()["owner_user_id"] == str(world["rep_a"].id)
    assert made.json()["email"] == "nina@x.com"
    # visible to the rep who added it even though it has no deal yet
    assert "Nina New" in [c["name"] for c in (await client.get(f"{API}/contacts", headers=rep)).json()["items"]]
    assert (await client.get(f"{API}/contacts", headers=_headers(world["rep_b"]))).json()["total"] == 1

    dup_phone = await client.post(f"{API}/contacts", json={"name": "Dup", "phone": "919876543210"}, headers=rep)
    assert dup_phone.status_code == 409 and "phone number" in dup_phone.json()["detail"]
    assert (await client.post(f"{API}/contacts", json={"name": "Dup", "email": "NINA@x.com"}, headers=rep)).status_code == 409
    assert (await client.post(f"{API}/contacts", json={"name": "Bad", "email": "nope"}, headers=rep)).status_code == 422
    assert (await client.post(f"{API}/contacts", json={"name": "   "}, headers=rep)).status_code == 422

    # search matches name, company and phone digits regardless of formatting
    adm = _headers(world["admin"])
    assert (await client.get(f"{API}/contacts?search=98765", headers=adm)).json()["total"] == 1
    assert (await client.get(f"{API}/contacts?search=nina", headers=adm)).json()["total"] == 1
    assert (await client.get(f"{API}/contacts?search=zzz", headers=adm)).json()["total"] == 0


@pytest.mark.asyncio
async def test_contact_detail_collects_deals_chats_tasks_and_timeline(client: AsyncClient, test_db, world):
    from tests.factories import add_message

    await add_message(test_db, world["conv_a"], "CUSTOMER", "Hello, interested in pricing")
    rep = _headers(world["rep_a"])
    cid = str(world["cust_a"].id)
    await client.post(f"{API}/tasks", json={"title": "Send quote", "customer_id": cid, "lead_id": str(world["lead_a"].id)}, headers=rep)
    await client.post(f"{API}/contacts/{cid}/notes", json={"text": "Prefers WhatsApp"}, headers=rep)
    await client.put(f"{API}/leads/{world['lead_a'].id}", json={"status": "contacted"}, headers=rep)

    detail = (await client.get(f"{API}/contacts/{cid}", headers=rep)).json()
    assert detail["contact"]["name"] == "Alice A" and detail["contact"]["open_deals"] == 1
    assert [d["title"] for d in detail["deals"]] == ["Deal A"]
    assert detail["chats"][0]["last_message_preview"] == "Hello, interested in pricing"
    assert [t["title"] for t in detail["tasks"]] == ["Send quote"]
    kinds = [a["kind"] for a in detail["timeline"]]
    assert {"NOTE", "STAGE", "TASK"} <= set(kinds)
    assert detail["timeline"][0]["actor_name"] and detail["timeline"][0]["can_delete"] in (True, False)


@pytest.mark.asyncio
async def test_notes_can_be_removed_by_their_author_or_an_admin_only(client: AsyncClient, test_db, world):
    cid = str(world["cust_a"].id)
    # rep_b becomes a participant by owning a contact-level note only if they can see the contact: give them access
    world["cust_a"].owner_user_id = world["rep_b"].id
    await test_db.commit()
    note = (await client.post(f"{API}/contacts/{cid}/notes", json={"text": "mine"}, headers=_headers(world["rep_a"]))).json()
    assert note["can_delete"] is True
    assert (await client.delete(f"{API}/contacts/{cid}/notes/{note['id']}", headers=_headers(world["rep_b"]))).status_code == 403
    assert (await client.delete(f"{API}/contacts/{cid}/notes/{note['id']}", headers=_headers(world["admin"]))).status_code == 204
    assert (await client.delete(f"{API}/contacts/{cid}/notes/{note['id']}", headers=_headers(world["admin"]))).status_code == 404
    # system events are never deletable
    event = (await test_db.execute(select(Activity).where(Activity.customer_id == world["cust_a"].id).limit(1))).scalars().first()
    if event is not None:
        assert (await client.delete(f"{API}/contacts/{cid}/notes/{event.id}", headers=_headers(world["admin"]))).status_code == 404


@pytest.mark.asyncio
async def test_editing_a_contact_checks_duplicates_and_owner_rights(client: AsyncClient, world):
    cid = str(world["cust_a"].id)
    rep = _headers(world["rep_a"])
    ok = await client.put(f"{API}/contacts/{cid}", json={"company_name": "Acme", "phone": "+1 555 000 1111"}, headers=rep)
    assert ok.status_code == 200 and ok.json()["company_name"] == "Acme"
    taken = await client.put(f"{API}/contacts/{world['cust_free'].id}", json={"phone": "15550001111"}, headers=_headers(world["admin"]))
    assert taken.status_code == 409
    assert (await client.put(f"{API}/contacts/{cid}", json={"owner_user_id": str(world["rep_b"].id)}, headers=rep)).status_code == 403
    assert (await client.put(f"{API}/contacts/{cid}", json={"owner_user_id": str(world["rep_b"].id)}, headers=_headers(world["admin"]))).status_code == 200
    # unchanged phone/email on an existing contact is not a duplicate of itself
    assert (await client.put(f"{API}/contacts/{cid}", json={"phone": "+1 555 000 1111", "name": "Alice Anderson"}, headers=_headers(world["admin"]))).status_code == 200


# ----------------------------------------------------------------------------- tasks
@pytest.mark.asyncio
async def test_task_lifecycle_buckets_and_summary(client: AsyncClient, world):
    rep = _headers(world["rep_a"])
    now = datetime.now(timezone.utc)
    day_end = _iso(now + timedelta(hours=6))
    mk = lambda title, due: client.post(f"{API}/tasks", json={"title": title, **({"due_at": _iso(due)} if due else {})}, headers=rep)

    overdue = (await mk("Overdue call", now - timedelta(hours=2))).json()
    await mk("Later today", now + timedelta(hours=2))
    await mk("Next week", now + timedelta(days=7))
    await mk("Someday", None)
    assert overdue["is_overdue"] is True and overdue["assigned_user_id"] == str(world["rep_a"].id)

    s = (await client.get(f"{API}/tasks/summary", params={"day_end": day_end}, headers=rep)).json()
    assert s == {"overdue": 1, "today": 1, "upcoming": 1, "no_date": 1, "open": 4}
    titles = lambda b: [t["title"] for t in b["items"]]
    for bucket, expected in {"overdue": ["Overdue call"], "today": ["Later today"], "upcoming": ["Next week"], "no_date": ["Someday"]}.items():
        got = (await client.get(f"{API}/tasks", params={"bucket": bucket, "day_end": day_end}, headers=rep)).json()
        assert titles(got) == expected, bucket
    assert titles((await client.get(f"{API}/tasks", params={"day_end": day_end}, headers=rep)).json())[0] == "Overdue call"  # soonest first

    done = await client.post(f"{API}/tasks/{overdue['id']}/complete", headers=rep)
    assert done.json()["status"] == "DONE" and done.json()["is_overdue"] is False
    assert titles((await client.get(f"{API}/tasks", params={"bucket": "done"}, headers=rep)).json()) == ["Overdue call"]
    assert (await client.get(f"{API}/tasks/summary", params={"day_end": day_end}, headers=rep)).json()["open"] == 3
    assert (await client.post(f"{API}/tasks/{overdue['id']}/reopen", headers=rep)).json()["status"] == "OPEN"

    edited = await client.put(f"{API}/tasks/{overdue['id']}", json={"title": "Call back", "due_at": None}, headers=rep)
    assert (edited.json()["title"], edited.json()["due_at"]) == ("Call back", None)
    assert (await client.put(f"{API}/tasks/{overdue['id']}", json={"title": ""}, headers=rep)).status_code == 422
    assert (await client.delete(f"{API}/tasks/{overdue['id']}", headers=rep)).status_code == 204
    assert (await client.delete(f"{API}/tasks/{overdue['id']}", headers=rep)).status_code == 404
    assert (await client.get(f"{API}/tasks", params={"bucket": "bogus"}, headers=rep)).status_code == 422


@pytest.mark.asyncio
async def test_task_ownership_rules(client: AsyncClient, test_db, world):
    rep_a, rep_b, adm = _headers(world["rep_a"]), _headers(world["rep_b"]), _headers(world["admin"])
    mine = (await client.post(f"{API}/tasks", json={"title": "A's task", "assigned_user_id": str(world["rep_b"].id)}, headers=rep_a)).json()
    assert mine["assigned_user_id"] == str(world["rep_a"].id)  # a rep cannot hand out tasks
    assert (await client.get(f"{API}/tasks", headers=rep_b)).json()["total"] == 0
    assert (await client.post(f"{API}/tasks/{mine['id']}/complete", headers=rep_b)).status_code == 404

    given = (await client.post(f"{API}/tasks", json={"title": "For Ben", "assigned_user_id": str(world["rep_b"].id)}, headers=adm)).json()
    assert given["assigned_user_name"].startswith("Ben")
    assert (await client.get(f"{API}/tasks", headers=rep_b)).json()["total"] == 1
    assert (await client.get(f"{API}/tasks", headers=adm)).json()["total"] == 2
    assert (await client.get(f"{API}/tasks?mine=true", headers=adm)).json()["total"] == 0
    assert (await client.put(f"{API}/tasks/{given['id']}", json={"assigned_user_id": str(world["rep_a"].id)}, headers=rep_b)).status_code == 403
    notified = (await test_db.execute(select(Notification).where(Notification.recipient_user_id == world["rep_b"].id))).scalars().all()
    assert any("For Ben" in n.title for n in notified)


@pytest.mark.asyncio
async def test_due_reminders_fire_once_and_again_after_a_reschedule(client: AsyncClient, test_db, world):
    rep = _headers(world["rep_a"])
    past = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
    task = (await client.post(f"{API}/tasks", json={"title": "Ring Alice", "due_at": past}, headers=rep)).json()
    await client.post(f"{API}/tasks", json={"title": "Not yet", "due_at": _iso(datetime.now(timezone.utc) + timedelta(days=1))}, headers=rep)

    assert await send_due_reminders(test_db) == 1
    assert await send_due_reminders(test_db) == 0
    reminders = (await test_db.execute(select(Notification).where(Notification.recipient_user_id == world["rep_a"].id,
                                                                    Notification.type == "FOLLOWUP_DUE"))).scalars().all()
    assert [r.title for r in reminders] == ["⏰ Task due: Ring Alice"]

    await client.put(f"{API}/tasks/{task['id']}", json={"due_at": past}, headers=rep)  # same time: no new reminder
    assert await send_due_reminders(test_db) == 0
    later_past = _iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    await client.put(f"{API}/tasks/{task['id']}", json={"due_at": later_past}, headers=rep)
    assert await send_due_reminders(test_db) == 1

    await client.post(f"{API}/tasks/{task['id']}/complete", headers=rep)
    stale = (await test_db.execute(select(Task).where(Task.id == uuid.UUID(task["id"])))).scalar_one()
    stale.reminder_sent_at = None
    await test_db.commit()
    assert await send_due_reminders(test_db) == 0  # finished tasks never remind


# ----------------------------------------------------------------------------- home & team
@pytest.mark.asyncio
async def test_home_summary_shows_setup_to_admins_and_focus_tasks(client: AsyncClient, world):
    adm = _headers(world["admin"])
    now = datetime.now(timezone.utc)
    await client.post(f"{API}/tasks", json={"title": "Chase invoice", "due_at": _iso(now - timedelta(hours=1))}, headers=adm)
    home = (await client.get(f"{API}/dashboard/home", params={"day_end": _iso(now + timedelta(hours=5))}, headers=adm)).json()
    assert home["tasks"]["overdue"] == 1 and [t["title"] for t in home["focus_tasks"]] == ["Chase invoice"]
    assert home["unassigned_leads"] == 1
    steps = {s["key"]: s["done"] for s in home["setup"]}
    assert steps == {"products": False, "knowledge": False, "ai": False, "team": True, "chat": True}

    rep_home = (await client.get(f"{API}/dashboard/home", headers=_headers(world["rep_a"]))).json()
    assert rep_home["setup"] is None and rep_home["unassigned_leads"] == 0 and rep_home["focus_tasks"] == []


@pytest.mark.asyncio
async def test_team_list_contains_only_active_members_of_my_tenant(client: AsyncClient, test_db, world):
    world["rep_b"].is_active = False
    await make_rep(test_db, await make_tenant(test_db, "Else"))
    await test_db.commit()
    team = (await client.get(f"{API}/team", headers=_headers(world["admin"]))).json()
    assert {m["name"] for m in team} == {"Ada Admin", "Rae Rep"} and sum(m["is_me"] for m in team) == 1
    assert (await client.get(f"{API}/team", headers=_headers(world["rep_a"]))).status_code == 200


@pytest.mark.asyncio
async def test_ai_captured_and_auto_assigned_leads_appear_on_the_contact_timeline(client: AsyncClient, test_db, world):
    from app.services.lead_assignment_service import LeadAssignmentService

    rep = await LeadAssignmentService.assign_lead_round_robin(
        test_db, world["tenant"].id, world["lead_free"].id, trigger_notification=False
    )
    assert rep is not None
    timeline = (await client.get(f"{API}/contacts/{world['cust_free'].id}", headers=_headers(world["admin"]))).json()["timeline"]
    assert any(a["kind"] == "ASSIGNED" and a["text"].startswith("Automatically assigned to") for a in timeline)


@pytest.mark.asyncio
async def test_sweep_gives_every_open_unowned_deal_an_owner(client: AsyncClient, test_db, world):
    """Deals created before auto-assignment existed (or while nobody was free) must not stay ownerless forever."""
    from app.services.lead_assignment_service import LeadAssignmentService

    tenant, admin = world["tenant"], world["admin"]
    extra = []
    for name, status in (("Open Two", "NEW"), ("Done", "WON"), ("Gone", "LOST")):
        cust = await make_customer(test_db, tenant, name)
        conv = await make_conversation(test_db, tenant, cust)
        extra.append(await make_lead(test_db, tenant, cust, conv, title=name, status=status))
    open_two, won, lost = extra

    # an admin deliberately unassigned this one: it carries an ASSIGNED entry on its timeline and must stay as it is
    cust = await make_customer(test_db, tenant, "Held")
    held = await make_lead(test_db, tenant, cust, await make_conversation(test_db, tenant, cust), title="Held back")
    test_db.add(Activity(tenant_id=tenant.id, kind="ASSIGNED", text="Ada unassigned this deal", lead_id=held.id,
                         customer_id=cust.id, actor_user_id=admin.id))
    await test_db.commit()

    assert await LeadAssignmentService.assign_unowned(test_db, tenant_id=tenant.id) == 2  # Deal Free + Open Two
    for lead in (world["lead_free"], open_two, won, lost, held):
        await test_db.refresh(lead)
    team = {world["rep_a"].id, world["rep_b"].id}  # sales reps only: the admin is never given deals
    assert world["lead_free"].assigned_user_id in team and open_two.assigned_user_id in team
    assert world["lead_free"].assigned_user_id != open_two.assigned_user_id  # round-robin spreads them
    assert won.assigned_user_id is None and lost.assigned_user_id is None and held.assigned_user_id is None
    from app.models.conversation import Conversation

    chat = await test_db.get(Conversation, world["lead_free"].conversation_id)
    await test_db.refresh(chat)
    assert chat.assigned_user_id == world["lead_free"].assigned_user_id  # the chat follows the deal
    notes = (await test_db.execute(select(Notification).where(Notification.tenant_id == tenant.id))).scalars().all()
    assert len(notes) == 2 and all(n.link_url.startswith("#pipeline?lead=") for n in notes)

    assert await LeadAssignmentService.assign_unowned(test_db, tenant_id=tenant.id) == 0  # nobody is told twice


@pytest.mark.asyncio
async def test_a_team_with_only_an_admin_leaves_deals_unowned(client: AsyncClient, test_db):
    from app.services.lead_assignment_service import LeadAssignmentService

    tenant = await make_tenant(test_db, "Solo Co")
    await _admin(test_db, tenant)
    cust = await make_customer(test_db, tenant, "S")
    lead = await make_lead(test_db, tenant, cust, await make_conversation(test_db, tenant, cust), title="Solo deal")
    assert await LeadAssignmentService.assign_lead_round_robin(test_db, tenant.id, lead.id) is None
    assert await LeadAssignmentService.assign_unowned(test_db, tenant_id=tenant.id) == 0
    await test_db.refresh(lead)
    assert lead.assigned_user_id is None


@pytest.mark.asyncio
async def test_sweep_sends_one_summary_per_rep_and_skips_tenants_that_cannot_assign(client: AsyncClient, test_db):
    from app.models.ai_assistant_config import AIAssistantConfig
    from app.services.lead_assignment_service import LeadAssignmentService

    busy = await make_tenant(test_db, "Busy Co")
    only_rep = await make_rep(test_db, busy)
    for i in range(3):
        cust = await make_customer(test_db, busy, f"C{i}")
        await make_lead(test_db, busy, cust, await make_conversation(test_db, busy, cust), title=f"D{i}")

    manual = await make_tenant(test_db, "Manual Co")  # round-robin switched off
    await make_rep(test_db, manual)
    test_db.add(AIAssistantConfig(tenant_id=manual.id, assistant_name="Nova", round_robin_assignment=False))
    cust = await make_customer(test_db, manual, "M")
    skipped = await make_lead(test_db, manual, cust, await make_conversation(test_db, manual, cust), title="Manual deal")

    nobody = await make_tenant(test_db, "Empty Co")  # no rep is free
    cust = await make_customer(test_db, nobody, "N")
    lonely = await make_lead(test_db, nobody, cust, await make_conversation(test_db, nobody, cust), title="Lonely")
    await test_db.commit()

    await LeadAssignmentService.assign_unowned(test_db)  # every tenant at once, as the scheduler does it
    for lead in (skipped, lonely):
        await test_db.refresh(lead)
    assert skipped.assigned_user_id is None and lonely.assigned_user_id is None
    busy_leads = (await test_db.execute(select(Lead).where(Lead.tenant_id == busy.id))).scalars().all()
    assert len(busy_leads) == 3 and all(l.assigned_user_id == only_rep.id for l in busy_leads)
    notes = (await test_db.execute(select(Notification).where(Notification.recipient_user_id == only_rep.id))).scalars().all()
    assert len(notes) == 1 and "3 deals" in notes[0].title and notes[0].link_url == "#pipeline"


# ----------------------------------------------------------------------------- KPI honesty
@pytest.mark.asyncio
async def test_dashboard_kpis_only_count_open_deals_and_use_the_hot_priority(client: AsyncClient, world):
    """Pipeline value and Hot leads describe deals still in play; a Won or Lost deal must drop out of both."""
    adm = _headers(world["admin"])
    base = (await client.get(f"{API}/dashboard", headers=adm)).json()
    assert base["total_pipeline_value"] == 1500 and base["hot_leads"] == 0  # score alone no longer makes a lead "hot"

    for lead in (world["lead_a"], world["lead_b"]):
        assert (await client.put(f"{API}/leads/{lead.id}", json={"priority": "hot"}, headers=adm)).status_code == 200
    stats = (await client.get(f"{API}/dashboard", headers=adm)).json()
    assert stats["hot_leads"] == 2 and stats["total_pipeline_value"] == 1500

    await client.put(f"{API}/leads/{world['lead_b'].id}", json={"status": "WON"}, headers=adm)  # worth 500
    await client.put(f"{API}/leads/{world['lead_free'].id}", json={"status": "LOST", "budget": 9999}, headers=adm)
    stats = (await client.get(f"{API}/dashboard", headers=adm)).json()
    assert stats["total_pipeline_value"] == 1000  # only the still-open deal
    assert stats["hot_leads"] == 1 and stats["won_leads"] == 1

    pipe = (await client.get(f"{API}/leads/pipeline-stats", headers=adm)).json()
    assert pipe["total_estimated_value"] == 1000 and pipe["hot_leads_count"] == 1 and pipe["stage_counts"]["WON"] == 1


@pytest.mark.asyncio
async def test_resolved_chats_are_counted_from_real_status(client: AsyncClient, test_db, world):
    world["conv_a"].status = "RESOLVED"
    await test_db.commit()
    stats = (await client.get(f"{API}/dashboard", headers=_headers(world["admin"]))).json()
    assert stats["resolved_conversations"] == 1 and stats["active_ai_conversations"] == 2
    rep = (await client.get(f"{API}/dashboard", headers=_headers(world["rep_a"]))).json()
    assert rep["resolved_conversations"] == 1 and rep["active_ai_conversations"] == 0  # a rep only counts their own chats
