"""
Shared pytest configuration — async test support and fixtures.
"""
import os

# Offline, deterministic embeddings for the whole suite (must be set before the app settings load).
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["VECTOR_BACKEND"] = "numpy"
os.environ["AI_WORKER_ENABLED"] = "false"

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.subscription_plan import SubscriptionPlan
from app.models.tenant import Tenant
from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# In-memory SQLite for tests — no real PostgreSQL required for unit tests.
# For integration tests, point TEST_DATABASE_URL at a real test DB.
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _ai_test_isolation(monkeypatch):
    """
    No test may reach a real LLM or reuse state from another test:
    the default LLM refuses every call (tests that need a model install a FakeLLM), and the in-process AI
    caches (retrieval index, circuit breakers, quota totals, query embeddings) start empty.
    """
    from app.core.config import settings
    from app.services.ai import llm as llm_mod
    from app.services.ai import quota
    from app.services.ai.vector_store import invalidate_tenant_index
    from app.services import rag_service

    from tests.helpers import NullLLM

    monkeypatch.setattr(settings, "GROQ_API_KEY", "")
    llm_mod.set_llm_override(NullLLM())
    llm_mod.reset_breakers()
    quota.reset_cache()
    invalidate_tenant_index()
    rag_service._query_cache.clear()
    yield
    llm_mod.set_llm_override(None)


@pytest.fixture()
def fake_llm():
    """A scriptable LLM installed for the duration of one test."""
    from app.services.ai import llm as llm_mod

    from tests.helpers import FakeLLM

    fake = FakeLLM()
    llm_mod.set_llm_override(fake)
    return fake


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def test_db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture()
async def client(test_db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP test client with DB dependency override."""
    async def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture()
async def super_admin_token(test_db: AsyncSession) -> str:
    user = User(
        first_name="Super",
        last_name="Admin",
        email="sa@test.com",
        password_hash="fake",
        role=UserRole.SUPER_ADMIN,
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return create_access_token(str(user.id), user.role.value, None)


@pytest_asyncio.fixture()
async def tenant_admin_token(test_db: AsyncSession) -> str:
    import uuid
    uid = uuid.uuid4().hex[:6]
    tenant = Tenant(name=f"Test Tenant {uid}", slug=f"test-tenant-{uid}")
    test_db.add(tenant)
    await test_db.flush()
    
    user = User(
        first_name="Tenant",
        last_name="Admin",
        email=f"ta_{uid}@test.com",
        password_hash="fake",
        role=UserRole.TENANT_ADMIN,
        tenant_id=tenant.id,
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return create_access_token(str(user.id), user.role.value, str(tenant.id))


@pytest_asyncio.fixture()
async def make_tenant_admin(test_db: AsyncSession):
    """
    Factory: create a tenant (optionally with a subscription plan of the given
    max_users) plus its Tenant Admin. Returns (token, tenant, admin_user).
    """
    import uuid

    async def _factory(max_users: int | None = 5, plan_active: bool = True):
        uid = uuid.uuid4().hex[:8]
        plan_id = None
        if max_users is not None:
            plan = SubscriptionPlan(
                name=f"Plan {uid}",
                slug=f"plan-{uid}",
                max_users=max_users,
                is_active=plan_active,
            )
            test_db.add(plan)
            await test_db.flush()
            plan_id = plan.id

        tenant = Tenant(name=f"Tenant {uid}", slug=f"tenant-{uid}", subscription_plan_id=plan_id)
        test_db.add(tenant)
        await test_db.flush()

        admin = User(
            first_name="Tenant",
            last_name="Admin",
            email=f"ta_{uid}@test.com",
            password_hash="fake",
            role=UserRole.TENANT_ADMIN,
            tenant_id=tenant.id,
            is_active=True,
        )
        test_db.add(admin)
        await test_db.commit()
        await test_db.refresh(admin)
        token = create_access_token(str(admin.id), admin.role.value, str(tenant.id))
        return token, tenant, admin

    return _factory


@pytest_asyncio.fixture()
async def sales_user_token(test_db: AsyncSession) -> str:
    import uuid
    uid = uuid.uuid4().hex[:6]
    tenant = Tenant(name=f"Sales Tenant {uid}", slug=f"sales-tenant-{uid}")
    test_db.add(tenant)
    await test_db.flush()
    
    user = User(
        first_name="Sales",
        last_name="User",
        email=f"su_{uid}@test.com",
        password_hash="fake",
        role=UserRole.SALES_USER,
        tenant_id=tenant.id,
        is_active=True,
    )
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    return create_access_token(str(user.id), user.role.value, str(tenant.id))
