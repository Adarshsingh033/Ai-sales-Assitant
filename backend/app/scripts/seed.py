"""
Super Admin database seeder.

Usage:
    cd backend
    python -m app.scripts.seed

Reads credentials from environment variables — never hardcoded.
Safe to run multiple times (idempotent).
"""
import asyncio
import logging
import sys
import uuid
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def seed_super_admin() -> None:
    """
    Create the Super Admin user if one does not already exist.
    Uses environment-provided credentials — no hardcoding.
    """
    async with AsyncSessionLocal() as db:
        try:
            repo = UserRepository(db)

            # Check existence
            existing = await repo.get_by_email(settings.SUPERADMIN_EMAIL)
            if existing is not None:
                logger.info("Super Admin already exists — skipping creation.")
                return

            # Hash password securely (Argon2)
            hashed = hash_password(settings.SUPERADMIN_PASSWORD)

            super_admin = User(
                id=uuid.uuid4(),
                tenant_id=None,              # Platform-level: no tenant
                first_name=settings.SUPERADMIN_FIRST_NAME,
                last_name=settings.SUPERADMIN_LAST_NAME,
                email=settings.SUPERADMIN_EMAIL.lower().strip(),
                password_hash=hashed,
                role=UserRole.SUPER_ADMIN,
                is_active=True,
                is_verified=True,
            )

            await repo.create(super_admin)
            await db.commit()

            logger.info(
                "Super Admin created successfully — id=%s email=%s",
                super_admin.id,
                super_admin.email,
            )

        except Exception:
            await db.rollback()
            logger.exception("Failed to seed Super Admin")
            sys.exit(1)


def main() -> None:
    asyncio.run(seed_super_admin())


if __name__ == "__main__":
    main()
