"""
Phase 1 MVP Data Models & Tenant Isolation Tests.
Verifies creation, relationships, cascade deletes, and tenant scoping for:
  - AIAssistantConfig
  - Product
  - KnowledgeDocument & KnowledgeChunk
  - Customer, Conversation & Message
  - Lead & LeadStatsProvider
"""
import json
import struct
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.product import Product
from app.models.knowledge import KnowledgeDocument, KnowledgeChunk
from app.models.customer import Customer
from app.models.conversation import Conversation, Message
from app.models.lead import Lead
from app.services.lead_stats_provider import LeadStatsProvider


@pytest.mark.asyncio
async def test_ai_assistant_config_crud(test_db: AsyncSession):
    tenant = Tenant(name="AI Config Tenant", slug="ai-config-tenant")
    test_db.add(tenant)
    await test_db.flush()

    config = AIAssistantConfig(
        tenant_id=tenant.id,
        assistant_name="Maya",
        personality="Consultative",
        custom_instructions="Focus on enterprise B2B sales.",
        max_discount_percent=12.5,
    )
    test_db.add(config)
    await test_db.commit()

    # Query back
    stmt = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant.id)
    saved = (await test_db.execute(stmt)).scalar_one()
    assert saved.assistant_name == "Maya"
    assert saved.personality == "Consultative"
    assert saved.max_discount_percent == 12.5
    assert saved.qualification_threshold == 60


@pytest.mark.asyncio
async def test_product_catalog_tenant_isolation(test_db: AsyncSession):
    t1 = Tenant(name="Tenant A", slug="tenant-a-prod")
    t2 = Tenant(name="Tenant B", slug="tenant-b-prod")
    test_db.add_all([t1, t2])
    await test_db.flush()

    p1 = Product(tenant_id=t1.id, name="Cloud Server", sku="CS-100", price=99.00)
    p2 = Product(tenant_id=t2.id, name="Dedicated Server", sku="DS-200", price=299.00)
    test_db.add_all([p1, p2])
    await test_db.commit()

    # Query products for Tenant A only
    stmt = select(Product).where(Product.tenant_id == t1.id)
    t1_products = (await test_db.execute(stmt)).scalars().all()
    assert len(t1_products) == 1
    assert t1_products[0].name == "Cloud Server"


@pytest.mark.asyncio
async def test_knowledge_document_and_chunks_cascade(test_db: AsyncSession):
    tenant = Tenant(name="Knowledge Tenant", slug="know-tenant")
    test_db.add(tenant)
    await test_db.flush()

    doc = KnowledgeDocument(
        tenant_id=tenant.id,
        filename="sales_faq.pdf",
        file_type="pdf",
        file_size=1024,
        status="INDEXED",
        chunk_count=2,
    )
    test_db.add(doc)
    await test_db.flush()

    dummy_embedding = struct.pack("<4f", 0.1, 0.2, 0.3, 0.4)
    chunk1 = KnowledgeChunk(
        tenant_id=tenant.id,
        document_id=doc.id,
        chunk_index=0,
        content="Pricing starts at $99 per user per month.",
        embedding=dummy_embedding,
        token_count=12,
    )
    chunk2 = KnowledgeChunk(
        tenant_id=tenant.id,
        document_id=doc.id,
        chunk_index=1,
        content="We provide 24/7 priority support on enterprise plans.",
        embedding=dummy_embedding,
        token_count=15,
    )
    test_db.add_all([chunk1, chunk2])
    await test_db.commit()

    # Verify chunks query
    stmt = select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id)
    chunks = (await test_db.execute(stmt)).scalars().all()
    assert len(chunks) == 2

    # Delete doc and verify cascade
    await test_db.delete(doc)
    await test_db.commit()

    chunks_after = (await test_db.execute(stmt)).scalars().all()
    assert len(chunks_after) == 0


@pytest.mark.asyncio
async def test_customer_conversation_and_messages(test_db: AsyncSession):
    tenant = Tenant(name="Conv Tenant", slug="conv-tenant")
    test_db.add(tenant)
    await test_db.flush()

    customer = Customer(
        tenant_id=tenant.id,
        name="John Doe",
        phone="+1234567890",
        email="john@example.com",
    )
    test_db.add(customer)
    await test_db.flush()

    conv = Conversation(
        tenant_id=tenant.id,
        customer_id=customer.id,
        channel="WHATSAPP",
        status="ACTIVE_AI",
    )
    test_db.add(conv)
    await test_db.flush()

    msg1 = Message(
        conversation_id=conv.id,
        sender_type="CUSTOMER",
        content="What is the price of the Cloud Server?",
    )
    msg2 = Message(
        conversation_id=conv.id,
        sender_type="AI",
        content="The Cloud Server starts at $99 per month.",
    )
    test_db.add_all([msg1, msg2])
    await test_db.commit()

    # Query conversation messages
    stmt = select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
    messages = (await test_db.execute(stmt)).scalars().all()
    assert len(messages) == 2
    assert messages[0].sender_type == "CUSTOMER"
    assert messages[1].sender_type == "AI"


@pytest.mark.asyncio
async def test_lead_creation_and_stats_provider(test_db: AsyncSession):
    tenant = Tenant(name="Lead Tenant", slug="lead-tenant")
    test_db.add(tenant)
    await test_db.flush()

    rep = User(
        first_name="Alice",
        last_name="Sales",
        email="alice.sales@example.com",
        password_hash="fake",
        role=UserRole.SALES_USER,
        tenant_id=tenant.id,
        is_active=True,
    )
    test_db.add(rep)
    await test_db.flush()

    lead1 = Lead(
        tenant_id=tenant.id,
        title="Enterprise Migration Project",
        status="QUALIFIED",
        score=85,
        score_reason="High budget, immediate timeline",
        budget=15000.00,
        timeline="Within 30 days",
        assigned_user_id=rep.id,
        priority="HOT",
    )
    lead2 = Lead(
        tenant_id=tenant.id,
        title="Small Business Starter",
        status="WON",  # Closed won
        score=90,
        assigned_user_id=rep.id,
    )
    test_db.add_all([lead1, lead2])
    await test_db.commit()

    # Check LeadStatsProvider
    provider = LeadStatsProvider(test_db)
    open_count = await provider.count_open_leads(tenant.id, rep.id)
    # lead1 is QUALIFIED (open), lead2 is WON (closed), so count must be 1
    assert open_count == 1
