from datetime import datetime
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_super_admin
from app.models.audit_log import AuditLogAction, AuditLogResourceType
from app.models.user import User
from app.schemas.audit_log import AuditLogListResponse, AuditLogResponse
from app.services.audit_service import AuditService

router = APIRouter(prefix="/super-admin/audit-logs", tags=["Super Admin Audit Logs"])


@router.get(
    "",
    response_model=AuditLogListResponse,
    summary="List Audit Logs",
    description="Retrieve a paginated list of audit logs across all tenants. Accessible only by Super Admin.",
)
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tenant_id: Optional[uuid.UUID] = None,
    user_id: Optional[uuid.UUID] = None,
    action: Optional[AuditLogAction] = None,
    resource_type: Optional[AuditLogResourceType] = None,
    resource_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> AuditLogListResponse:
    service = AuditService(db)
    return await service.list_audit_logs(
        page=page,
        page_size=page_size,
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "/{log_id}",
    response_model=AuditLogResponse,
    summary="Get Audit Log Details",
    description="Retrieve specific details of an audit log. Accessible only by Super Admin.",
)
async def get_audit_log(
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
) -> AuditLogResponse:
    service = AuditService(db)
    return await service.get_audit_log(log_id)
