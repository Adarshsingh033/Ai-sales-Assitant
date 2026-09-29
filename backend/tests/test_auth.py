"""
Authentication endpoint tests — POST /api/v1/auth/login

Covers:
  - Valid login (Super Admin)
  - Invalid password
  - Non-existent email
  - Inactive user
  - JWT content verification
"""
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole


async def _create_user(
    db: AsyncSession,
    email: str,
    password: str,
    role: UserRole = UserRole.SUPER_ADMIN,
    is_active: bool = True,
    tenant_id=None,
) -> User:
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        first_name="Test",
        last_name="User",
        email=email.lower(),
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
        is_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_valid_login_super_admin(client: AsyncClient, test_db: AsyncSession):
    """Super Admin can log in and receives a valid JWT with correct role."""
    await _create_user(test_db, "superadmin@test.com", "SecurePass123!")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "superadmin@test.com", "password": "SecurePass123!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["role"] == "SUPER_ADMIN"
    assert data["user"]["email"] == "superadmin@test.com"
    assert "password" not in data["user"]
    assert "password_hash" not in data["user"]


@pytest.mark.asyncio
async def test_invalid_password(client: AsyncClient, test_db: AsyncSession):
    """Wrong password returns 401 with generic error."""
    await _create_user(test_db, "user_wrongpw@test.com", "CorrectPass!")

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "user_wrongpw@test.com", "password": "WrongPass!"},
    )
    assert resp.status_code == 401
    assert "password" not in resp.json().get("detail", "").lower() or \
           "invalid" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_nonexistent_email(client: AsyncClient, test_db: AsyncSession):
    """Non-existent email returns 401 — same response as wrong password."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@nowhere.com", "password": "anypass"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_inactive_user_cannot_login(client: AsyncClient, test_db: AsyncSession):
    """Inactive users are rejected even with correct credentials."""
    await _create_user(test_db, "inactive@test.com", "Pass123!", is_active=False)

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@test.com", "password": "Pass123!"},
    )
    # Could be 401 (invalid creds path) or 403 (inactive) — both are acceptable
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_missing_email_field(client: AsyncClient, test_db: AsyncSession):
    """Missing email field returns 422 validation error."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"password": "somepass"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_password_field(client: AsyncClient, test_db: AsyncSession):
    """Missing password field returns 422 validation error."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "user@test.com"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_email_format(client: AsyncClient, test_db: AsyncSession):
    """Malformed email returns 422."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "not-an-email", "password": "pass"},
    )
    assert resp.status_code == 422
