"""
User model and seeder logic tests.

Covers:
  - Super Admin creation
  - Duplicate Super Admin prevention
  - Role enum values
  - Password hash — never plain text
"""
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository


@pytest.mark.asyncio
async def test_create_super_admin(test_db: AsyncSession):
    """Super Admin is created with correct fields."""
    repo = UserRepository(test_db)
    hashed = hash_password("TestSecret123!")

    user = User(
        id=uuid.uuid4(),
        tenant_id=None,
        first_name="Super",
        last_name="Admin",
        email="sa@platform.com",
        password_hash=hashed,
        role=UserRole.SUPER_ADMIN,
        is_active=True,
        is_verified=True,
    )
    created = await repo.create(user)
    await test_db.commit()

    assert created.id is not None
    assert created.tenant_id is None
    assert created.role == UserRole.SUPER_ADMIN
    assert created.is_active is True
    assert created.is_verified is True
    # Password must be hashed — never plain text
    assert created.password_hash != "TestSecret123!"
    assert verify_password("TestSecret123!", created.password_hash)


@pytest.mark.asyncio
async def test_super_admin_duplicate_prevention(test_db: AsyncSession):
    """email_exists returns True after creation, preventing duplicate seeds."""
    repo = UserRepository(test_db)

    user = User(
        id=uuid.uuid4(),
        tenant_id=None,
        first_name="Another",
        last_name="Admin",
        email="only-one@platform.com",
        password_hash=hash_password("pass"),
        role=UserRole.SUPER_ADMIN,
        is_active=True,
        is_verified=True,
    )
    await repo.create(user)
    await test_db.commit()

    exists = await repo.email_exists("only-one@platform.com")
    assert exists is True


@pytest.mark.asyncio
async def test_inactive_user_flag(test_db: AsyncSession):
    """Inactive flag is persisted correctly."""
    repo = UserRepository(test_db)
    user = User(
        id=uuid.uuid4(),
        tenant_id=None,
        first_name="Inactive",
        last_name="User",
        email="inactive-user@platform.com",
        password_hash=hash_password("pass"),
        role=UserRole.SALES_USER,
        is_active=False,
        is_verified=False,
    )
    created = await repo.create(user)
    await test_db.commit()

    fetched = await repo.get_by_email("inactive-user@platform.com")
    assert fetched is not None
    assert fetched.is_active is False


def test_password_hashing_and_verification():
    """Argon2 hash produces a different string; verification works correctly."""
    plain = "MySecurePass!99"
    hashed = hash_password(plain)
    assert hashed != plain
    assert verify_password(plain, hashed)
    assert not verify_password("wrong", hashed)


def test_user_role_enum_values():
    """Enum values match the expected string literals used in JWT and DB."""
    assert UserRole.SUPER_ADMIN.value == "SUPER_ADMIN"
    assert UserRole.TENANT_ADMIN.value == "TENANT_ADMIN"
    assert UserRole.SALES_USER.value == "SALES_USER"
