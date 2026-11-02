from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _strip_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class SalesUserCreate(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    phone_number: Optional[str] = Field(None, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    is_available: bool = True
    role_id: Optional[UUID] = None  # one of the tenant's roles; omitted = the built-in "Sales Rep"

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip_names(cls, value: str) -> str:
        return _strip_required(value)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.lower().strip()


class SalesUserUpdate(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=20)
    is_available: Optional[bool] = None
    role_id: Optional[UUID] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip_names(cls, value: Optional[str]) -> Optional[str]:
        return _strip_required(value) if value is not None else value


class SalesUserStatusUpdate(BaseModel):
    is_active: bool


class SalesUserResetPassword(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=128)


class SalesUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    first_name: str
    last_name: str
    email: str
    phone_number: Optional[str] = None
    profile_image_url: Optional[str] = None
    role: str  # access level: SALES_USER or TENANT_ADMIN
    role_id: Optional[UUID] = None
    role_name: str = ""  # the admin-defined title shown to people
    receives_deals: bool = False
    is_me: bool = False
    is_active: bool
    is_available: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class SalesUserStatusResponse(SalesUserResponse):
    has_open_leads: bool = False
    open_leads_count: int = 0


class SalesUserListResponse(BaseModel):
    items: List[SalesUserResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
