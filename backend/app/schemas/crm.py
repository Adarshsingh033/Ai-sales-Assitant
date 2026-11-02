"""
Pydantic schemas for the CRM workspace: contacts, tasks, timeline, deal board and the home summary.
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


# ----------------------------------------------------------------------------- tasks
class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    notes: Optional[str] = Field(None, max_length=2000)
    due_at: Optional[datetime] = None
    customer_id: Optional[uuid.UUID] = None
    lead_id: Optional[uuid.UUID] = None
    assigned_user_id: Optional[uuid.UUID] = None  # admins only; reps always own what they create

    _strip = field_validator("title", "notes", mode="before")(_blank_to_none)


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    notes: Optional[str] = Field(None, max_length=2000)
    due_at: Optional[datetime] = None
    assigned_user_id: Optional[uuid.UUID] = None  # admins only

    _strip = field_validator("title", "notes", mode="before")(_blank_to_none)


class TaskResponse(BaseModel):
    id: uuid.UUID
    title: str
    notes: Optional[str] = None
    due_at: Optional[datetime] = None
    status: str
    is_overdue: bool = False
    completed_at: Optional[datetime] = None
    customer_id: Optional[uuid.UUID] = None
    customer_name: Optional[str] = None
    lead_id: Optional[uuid.UUID] = None
    lead_title: Optional[str] = None
    assigned_user_id: Optional[uuid.UUID] = None
    assigned_user_name: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskListResponse(BaseModel):
    items: List[TaskResponse]
    total: int
    page: int
    page_size: int


class TaskSummary(BaseModel):
    overdue: int
    today: int
    upcoming: int
    no_date: int
    open: int


# ----------------------------------------------------------------------------- timeline
class ActivityResponse(BaseModel):
    id: uuid.UUID
    kind: str
    text: str
    actor_name: Optional[str] = None
    lead_id: Optional[uuid.UUID] = None
    created_at: datetime
    can_delete: bool = False


class NoteCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    lead_id: Optional[uuid.UUID] = None

    _strip = field_validator("text", mode="before")(_blank_to_none)


# ----------------------------------------------------------------------------- contacts
class ContactCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    company_name: Optional[str] = Field(None, max_length=255)
    owner_user_id: Optional[uuid.UUID] = None  # admins only; a rep always owns what they add

    _strip = field_validator("name", "phone", "email", "company_name", mode="before")(_blank_to_none)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v):
        if v and ("@" not in v or " " in v or v.startswith("@") or v.endswith("@")):
            raise ValueError("Enter a valid email address")
        return v.lower() if v else v


class ContactUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    company_name: Optional[str] = Field(None, max_length=255)
    owner_user_id: Optional[uuid.UUID] = None  # admins only

    _strip = field_validator("name", "phone", "email", "company_name", mode="before")(_blank_to_none)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v):
        if v and ("@" not in v or " " in v or v.startswith("@") or v.endswith("@")):
            raise ValueError("Enter a valid email address")
        return v.lower() if v else v


class ContactSummary(BaseModel):
    id: uuid.UUID
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    company_name: Optional[str] = None
    owner_user_id: Optional[uuid.UUID] = None
    owner_name: Optional[str] = None
    open_deals: int = 0
    last_activity_at: Optional[datetime] = None
    do_not_contact: bool = False
    created_at: datetime


class ContactListResponse(BaseModel):
    items: List[ContactSummary]
    total: int
    page: int
    page_size: int
    pages: int


class ContactDeal(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    priority: str
    score: int
    budget: Optional[float] = None
    assigned_user_id: Optional[uuid.UUID] = None
    assigned_user_name: Optional[str] = None
    created_at: datetime


class ContactChat(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    last_message_preview: Optional[str] = None
    last_activity_at: datetime
    assigned_user_name: Optional[str] = None


class ContactDetail(BaseModel):
    contact: ContactSummary
    deals: List[ContactDeal]
    chats: List[ContactChat]
    tasks: List[TaskResponse]
    timeline: List[ActivityResponse]


# ----------------------------------------------------------------------------- deals
class DealCreate(BaseModel):
    customer_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=255)
    budget: Optional[float] = Field(None, ge=0)
    status: str = "NEW"
    priority: str = "MEDIUM"
    notes: Optional[str] = Field(None, max_length=2000)
    assigned_user_id: Optional[uuid.UUID] = None  # admins only; a rep always owns what they create

    _strip = field_validator("title", "notes", mode="before")(_blank_to_none)


class BoardCard(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    priority: str
    score: int
    budget: Optional[float] = None
    customer_id: Optional[uuid.UUID] = None
    customer_name: Optional[str] = None
    assigned_user_id: Optional[uuid.UUID] = None
    assigned_user_name: Optional[str] = None
    open_tasks: int = 0
    overdue_tasks: int = 0
    updated_at: datetime


class BoardColumn(BaseModel):
    stage: str
    label: str
    count: int
    total_value: float
    cards: List[BoardCard]


class BoardResponse(BaseModel):
    columns: List[BoardColumn]
    unassigned: int = 0


# ----------------------------------------------------------------------------- home
class SetupStep(BaseModel):
    key: str
    title: str
    hint: str
    done: bool
    target: str  # sidebar section to open


class HomeSummary(BaseModel):
    tasks: TaskSummary
    focus_tasks: List[TaskResponse]
    unassigned_leads: int = 0
    needs_human: int = 0
    unanswered_questions: int = 0  # admins: questions the team asked that the knowledge base could not answer
    setup: Optional[List[SetupStep]] = None
    stage_counts: Dict[str, int] = {}
