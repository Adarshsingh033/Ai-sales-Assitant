"""Small async factories shared by the AI test modules."""
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.lead import Lead
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User, UserRole


async def make_tenant(db, name: str = "Acme", timezone: Optional[str] = None) -> Tenant:
    t = Tenant(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}", timezone=timezone)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def make_config(db, tenant: Tenant, **overrides) -> AIAssistantConfig:
    cfg = AIAssistantConfig(tenant_id=tenant.id, **overrides)
    db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    return cfg


async def make_product(db, tenant: Tenant, name="Enterprise CRM Suite", price=999.0, sku="CRM-ENT", **kw) -> Product:
    p = Product(tenant_id=tenant.id, name=name, sku=sku, price=price, currency="USD", category="Software",
                description=kw.pop("description", "Full-featured enterprise CRM platform."), **kw)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def make_customer(db, tenant: Tenant, name="Jordan Smith", phone: Optional[str] = None) -> Customer:
    c = Customer(tenant_id=tenant.id, name=name, phone=phone or f"+1555{uuid.uuid4().int % 10**7:07d}")
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def make_conversation(db, tenant: Tenant, customer: Customer, channel="WEB_SIMULATOR", status="ACTIVE_AI") -> Conversation:
    conv = Conversation(tenant_id=tenant.id, customer_id=customer.id, channel=channel, status=status)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def make_lead(db, tenant, customer, conv, **kw) -> Lead:
    lead = Lead(tenant_id=tenant.id, customer_id=customer.id, conversation_id=conv.id,
                title=kw.pop("title", "Enterprise CRM Demo"), score=kw.pop("score", 75), **kw)
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return lead


async def make_rep(db, tenant: Tenant) -> User:
    u = User(first_name="Rae", last_name="Rep", email=f"rep_{uuid.uuid4().hex[:8]}@t.com", password_hash="x",
             role=UserRole.SALES_USER, tenant_id=tenant.id, is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def add_message(db, conv: Conversation, sender: str, content: str, created_at=None) -> Message:
    m = Message(conversation_id=conv.id, sender_type=sender, content=content)
    if created_at is not None:
        m.created_at = created_at
    db.add(m)
    await db.commit()
    return m


def session_factory(db):
    """A `jobs` session factory that hands out the test session."""
    @asynccontextmanager
    async def factory():
        yield db
    return factory


async def auth_headers(db, tenant: Tenant, role: UserRole = UserRole.TENANT_ADMIN) -> dict:
    from app.core.security import create_access_token

    u = User(first_name="Ada", last_name="Admin", email=f"adm_{uuid.uuid4().hex[:8]}@t.com", password_hash="x",
             role=role, tenant_id=tenant.id, is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return {"Authorization": f"Bearer {create_access_token(str(u.id), u.role.value, str(tenant.id))}"}
