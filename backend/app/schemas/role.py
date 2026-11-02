import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


def _clean_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("must not be blank")
    return value


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    access: Literal["SALES", "ADMIN"] = "SALES"
    receives_deals: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_name(value)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    receives_deals: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: Optional[str]) -> Optional[str]:
        return _clean_name(value) if value is not None else value


class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    access: str
    receives_deals: bool
    is_system: bool
    member_count: int = 0
