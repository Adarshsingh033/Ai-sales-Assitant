"""
Models package — import all models here so Alembic autogeneration can discover them.
"""
from app.models.tenant import Tenant
from app.models.user import User, UserRole

__all__ = ["Tenant", "User", "UserRole"]
