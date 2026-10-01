from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field

class UserProfileResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: EmailStr
    role: str
    tenant_id: Optional[UUID] = None
    phone_number: Optional[str] = None
    profile_image_url: Optional[str] = None

    model_config = {"from_attributes": True}


class UserProfileUpdateRequest(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone_number: Optional[str] = Field(None, max_length=20)
    profile_image_url: Optional[str] = Field(None)

class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)

