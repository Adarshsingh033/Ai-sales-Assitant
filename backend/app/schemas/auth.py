"""
Pydantic schemas for authentication endpoints.
These are strictly input/output contracts — they never expose sensitive fields.
"""
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=1, description="User password")

    model_config = {"json_schema_extra": {"example": {"email": "admin@example.com", "password": "password"}}}


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class AuthenticatedUser(BaseModel):
    """Safe user representation — never includes password_hash or other secrets."""
    id: UUID
    first_name: str
    last_name: str
    email: str
    role: str
    tenant_id: Optional[UUID] = None

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthenticatedUser
