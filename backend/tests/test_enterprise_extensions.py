"""
Comprehensive Integration Tests for Phase 1 Enterprise Extensions:
1. AI Usage & Token Tracking
2. Round-Robin Lead Assignment
3. Automated Follow-Ups for Qualifying Leads
4. Warehouse & Product Inventory ERP
5. In-App Notifications
"""
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.product import Product
from app.models.customer import Customer
from app.models.conversation import Conversation
from app.models.lead import Lead
from app.models.ai_assistant_config import AIAssistantConfig
from app.core.security import create_access_token
from app.services.ai_usage_tracker import AIUsageTracker
from app.services.lead_assignment_service import LeadAssignmentService
from app.services.follow_up_service import FollowUpService
from app.services.inventory_service import InventoryService
from app.services.notification_service import NotificationService


@pytest.fixture()
async def tenant_admin_and_reps(test_db: AsyncSession):
    uid = uuid.uuid4().hex[:6]
    tenant = Tenant(name=f"Enterprise Corp {uid}", slug=f"ent-corp-{uid}")
    test_db.add(tenant)
    await test_db.flush()

    admin = User(
        first_name="Admin",
        last_name="Manager",
        email=f"admin_{uid}@entcorp.com",
        password_hash="hashed_pw",
        role=UserRole.TENANT_ADMIN,
        tenant_id=tenant.id,
        is_active=True,
        is_available=True,
    )
    rep1 = User(
        first_name="Alice",
        last_name="Sales",
        email=f"alice_{uid}@entcorp.com",
        password_hash="hashed_pw",
        role=UserRole.SALES_USER,
        tenant_id=tenant.id,
        is_active=True,
        is_available=True,
    )
    rep2 = User(
        first_name="Bob",
        last_name="Sales",
        email=f"bob_{uid}@entcorp.com",
        password_hash="hashed_pw",
        role=UserRole.SALES_USER,
        tenant_id=tenant.id,
        is_active=True,
        is_available=True,
    )
    test_db.add_all([admin, rep1, rep2])
    await test_db.flush()

    cfg = AIAssistantConfig(
        tenant_id=tenant.id,
        assistant_name="Nova",
        round_robin_assignment=True,
        auto_followup_enabled=True,
        followup_delay_hours=24,
    )
    test_db.add(cfg)
    await test_db.commit()

    token = create_access_token(str(admin.id), admin.role.value, str(tenant.id))
    return token, tenant, admin, rep1, rep2


@pytest.mark.asyncio
async def test_ai_usage_monitoring_tracking_and_stats(client: AsyncClient, test_db: AsyncSession, tenant_admin_and_reps):
    token, tenant, admin, rep1, rep2 = tenant_admin_and_reps
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Log several usage events
    await AIUsageTracker.log_usage(
        db=test_db,
        tenant_id=tenant.id,
        model="llama-3.3-70b-versatile",
        action_type="SALES_CHAT",
        prompt_tokens=150,
        completion_tokens=50,
        latency_ms=280,
    )
    await AIUsageTracker.log_usage(
        db=test_db,
        tenant_id=tenant.id,
        model="llama-3.3-70b-versatile",
        action_type="LEAD_EXTRACTION",
        prompt_tokens=300,
        completion_tokens=100,
        latency_ms=320,
    )
    await test_db.commit()

    # 2. Query stats endpoint
    res = await client.get("/api/v1/tenant-admin/ai-usage/stats?days=30", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["total_tokens"] == 600
    assert data["prompt_tokens"] == 450
    assert data["completion_tokens"] == 150
    assert data["total_requests"] == 2
    assert data["avg_latency_ms"] >= 280
    assert data["estimated_savings_usd"] >= 0.0


@pytest.mark.asyncio
async def test_round_robin_lead_assignment(client: AsyncClient, test_db: AsyncSession, tenant_admin_and_reps):
    token, tenant, admin, rep1, rep2 = tenant_admin_and_reps

    # Create 2 leads
    lead1 = Lead(tenant_id=tenant.id, title="Lead 1", score=85, priority="HOT")
    lead2 = Lead(tenant_id=tenant.id, title="Lead 2", score=90, priority="HOT")
    test_db.add_all([lead1, lead2])
    await test_db.commit()

    # Assign lead 1 -> Should pick either rep1 or rep2, never the admin
    first_assigned = await LeadAssignmentService.assign_lead_round_robin(
        db=test_db,
        tenant_id=tenant.id,
        lead_id=lead1.id,
        trigger_notification=True,
    )
    assert first_assigned is not None
    assert first_assigned.id in [rep1.id, rep2.id]
    assert first_assigned.id != admin.id

    # Assign lead 2 -> Must pick a DIFFERENT rep
    second_assigned = await LeadAssignmentService.assign_lead_round_robin(
        db=test_db,
        tenant_id=tenant.id,
        lead_id=lead2.id,
        trigger_notification=True,
    )
    assert second_assigned is not None
    assert second_assigned.id != first_assigned.id


@pytest.mark.asyncio
async def test_warehouse_and_product_inventory_erp(client: AsyncClient, test_db: AsyncSession, tenant_admin_and_reps):
    token, tenant, admin, rep1, rep2 = tenant_admin_and_reps
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create product
    prod = Product(tenant_id=tenant.id, name="Cloud ERP License", price=1200.0, sku="ERP-01")
    test_db.add(prod)
    await test_db.commit()
    await test_db.refresh(prod)

    # 2. List warehouses (auto-provisions default warehouse)
    wh_res = await client.get("/api/v1/tenant-admin/inventory/warehouses", headers=headers)
    assert wh_res.status_code == 200
    warehouses = wh_res.json()
    assert len(warehouses) >= 1
    wh_id = warehouses[0]["id"]

    # 3. Update stock levels
    stock_payload = {
        "product_id": str(prod.id),
        "warehouse_id": wh_id,
        "quantity_on_hand": 100,
        "quantity_reserved": 15,
        "low_stock_threshold": 10,
    }
    up_res = await client.put("/api/v1/tenant-admin/inventory/stock", headers=headers, json=stock_payload)
    assert up_res.status_code == 200
    stock_data = up_res.json()
    assert stock_data["quantity_on_hand"] == 100
    assert stock_data["quantity_reserved"] == 15
    assert stock_data["quantity_available"] == 85

    # 4. Verify context string generated for AI
    ai_context = await InventoryService.get_inventory_context_for_ai(test_db, tenant.id)
    assert "Cloud ERP License: 85 units available [IN STOCK]" in ai_context


@pytest.mark.asyncio
async def test_notifications_and_unread_counts(client: AsyncClient, test_db: AsyncSession, tenant_admin_and_reps):
    token, tenant, admin, rep1, rep2 = tenant_admin_and_reps
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create notifications
    notif1 = await NotificationService.create_notification(
        db=test_db,
        tenant_id=tenant.id,
        title="🔥 Hot Opportunity",
        message="Prospect requested immediate deployment.",
        notif_type="HOT_LEAD",
        recipient_user_id=admin.id,
    )
    notif2 = await NotificationService.create_notification(
        db=test_db,
        tenant_id=tenant.id,
        title="🚨 Human Takeover Required",
        message="Customer requested live human.",
        notif_type="HUMAN_TAKEOVER_REQUESTED",
        recipient_user_id=admin.id,
    )

    # 2. Check unread count
    count_res = await client.get("/api/v1/tenant-admin/notifications/unread-count", headers=headers)
    assert count_res.status_code == 200
    assert count_res.json()["unread_count"] >= 2

    # 3. Mark single as read
    mark_res = await client.post(f"/api/v1/tenant-admin/notifications/{notif1.id}/read", headers=headers)
    assert mark_res.status_code == 200

    # 4. Mark all as read
    all_res = await client.post("/api/v1/tenant-admin/notifications/read-all", headers=headers)
    assert all_res.status_code == 200

    # Verify unread count is now 0
    final_count = await client.get("/api/v1/tenant-admin/notifications/unread-count", headers=headers)
    assert final_count.json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_automated_follow_up_lifecycle(client: AsyncClient, test_db: AsyncSession, tenant_admin_and_reps):
    token, tenant, admin, rep1, rep2 = tenant_admin_and_reps
    headers = {"Authorization": f"Bearer {token}"}

    # Setup customer, conversation, and lead
    cust = Customer(tenant_id=tenant.id, name="Jordan Smith", phone="+15550199002")
    test_db.add(cust)
    await test_db.flush()

    conv = Conversation(tenant_id=tenant.id, customer_id=cust.id, channel="WEB_SIMULATOR", status="ACTIVE_AI")
    test_db.add(conv)
    await test_db.flush()

    lead = Lead(
        tenant_id=tenant.id,
        customer_id=cust.id,
        conversation_id=conv.id,
        title="Enterprise CRM Demo",
        score=75,
        priority="HOT",
        requirements="Enterprise CRM for 20 reps",
    )
    test_db.add(lead)
    await test_db.commit()

    # 1. Schedule follow-up with delay = 0 (immediately due)
    sched = await FollowUpService.schedule_followup_if_eligible(
        db=test_db,
        tenant_id=tenant.id,
        lead_id=lead.id,
        customer_id=cust.id,
        conversation_id=conv.id,
        delay_hours=0,
    )
    assert sched is not None
    assert sched.status == "PENDING"

    # 2. List schedules API
    list_res = await client.get("/api/v1/tenant-admin/follow-ups", headers=headers)
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) >= 1

    # 3. Trigger due follow-ups process
    proc_res = await client.post("/api/v1/tenant-admin/follow-ups/process-due", headers=headers)
    assert proc_res.status_code == 200
    assert proc_res.json()["dispatched_count"] >= 1

    # Verify schedule transitioned to DISPATCHED
    await test_db.refresh(sched)
    assert sched.status == "DISPATCHED"
    assert sched.executed_at is not None
    assert "Jordan Smith" in sched.custom_message
