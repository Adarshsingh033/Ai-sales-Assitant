import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.audit_log import AuditLogAction, AuditLogResourceType


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    user_id: Optional[uuid.UUID]
    action: AuditLogAction
    resource_type: AuditLogResourceType
    resource_id: Optional[str]
    description: Optional[str]
    old_values: Optional[Dict[str, Any]]
    new_values: Optional[Dict[str, Any]]
    ip_address: Optional[str]
    user_agent: Optional[str]
    request_id: Optional[str]
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: List[AuditLogResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
