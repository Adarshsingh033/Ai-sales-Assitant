"""
Shared pytest configuration — async test support and fixtures.
"""
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
from app.models.tenant import Tenant
from app.models.user import User, UserRole

# ---------------------------------------------------------------------------
# In-memory SQLite for tests — no real PostgreSQL required for unit tests.
# For integration tests, point TEST_DATABASE_URL at a real test DB.
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
