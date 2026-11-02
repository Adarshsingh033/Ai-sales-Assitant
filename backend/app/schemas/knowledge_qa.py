"""
Schemas for the internal "Ask the knowledge base" chat.
"""
import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class AskTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(..., max_length=1500)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)
    history: List[AskTurn] = Field(default_factory=list, max_length=8)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Please type a question")
        return v


class AskSource(BaseModel):
    n: int
    kind: str  # DOCUMENT | PRODUCT
    title: str
    location: Optional[str] = None
    text: str


class AskResponse(BaseModel):
    status: Literal["answered", "not_found", "not_stated", "unverified", "empty"]
    answer: str
    sources: List[AskSource] = Field(default_factory=list)
    searched_for: Optional[str] = None  # the standalone query used for a follow-up


class LibraryDocument(BaseModel):
    id: uuid.UUID
    title: str
    file_type: str
    status: str
    updated_at: datetime


class LibraryResponse(BaseModel):
    documents: List[LibraryDocument]
    product_count: int
    product_names: List[str] = Field(default_factory=list)  # a few real names, for example questions in the UI


class GapItem(BaseModel):
    id: uuid.UUID
    question: str
    times_asked: int
    status: str
    last_asked_by: Optional[str] = None
    last_asked_at: datetime


class GapListResponse(BaseModel):
    items: List[GapItem]
    open_count: int
