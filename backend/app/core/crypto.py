"""
Symmetric encryption for secrets stored in the database (tenant API keys, channel tokens).

Values are stored as `enc:v1:<fernet-token>`. Plain-text legacy values (no prefix) are still
readable so existing rows keep working until they are next saved or migrated.
"""
import base64
import hashlib
import logging
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = (settings.ENCRYPTION_KEY or "").strip()
    if key:
        return Fernet(key.encode())
    logger.warning("ENCRYPTION_KEY is not set; deriving the secrets key from JWT_SECRET_KEY.")
    digest = hashlib.sha256(("secrets:" + settings.JWT_SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and value.startswith(_PREFIX)


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    if is_encrypted(value):
        return value
    return _PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """Return the plain value; legacy plain-text values pass through unchanged."""
    if not value:
        return value
    if not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt a stored secret (wrong ENCRYPTION_KEY?).")
        return None
