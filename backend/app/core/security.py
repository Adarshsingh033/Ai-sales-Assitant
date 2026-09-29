"""
Security utilities — password hashing (Argon2) and JWT token management.
Never log passwords, hashes, or JWT secrets.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from jose import jwt

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Argon2 password hasher — OWASP-recommended defaults
# ---------------------------------------------------------------------------
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,  # 64 MB
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


def hash_password(plain_password: str) -> str:
    """Hash a plain-text password using Argon2id. Returns the encoded hash string."""
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """
    Verify plain_password against the stored Argon2 hash.
    Returns True on success, False on mismatch. Never raises externally.
    """
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception:
        logger.exception("Unexpected error during password verification")
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(
    subject: str,
    role: str,
    tenant_id: Optional[str],
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate a signed JWT access token.

    Payload contains only: sub, role, tenant_id, exp, iat.
    Do NOT add sensitive information.
    """
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: Dict[str, Any] = {
        "sub": subject,
        "role": role,
        "tenant_id": tenant_id,
        "iat": now,
        "exp": expire,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Decode and verify a JWT access token.
    Raises JWTError (jose) on any failure — callers must handle it.
    """
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
