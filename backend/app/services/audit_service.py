import math
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog, AuditLogAction, AuditLogResourceType
from app.repositories.audit_log_repository import AuditLogRepository
from app.schemas.audit_log import AuditLogListResponse, AuditLogResponse


def _sanitize_data(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Recursively scrub sensitive information before logging."""
    if not data:
        return None
    
    sensitive_keys = {"password", "password_hash", "token", "access_token", "refresh_token", "secret"}
    sanitized = {}
    for k, v in data.items():
        if any(sens in k.lower() for sens in sensitive_keys):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_data(v)
        elif isinstance(v, uuid.UUID):
            sanitized[k] = str(v)
        else:
            sanitized[k] = v
    return sanitized


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = AuditLogRepository(db)

    async def create_audit_log(
        self,
        action: AuditLogAction,
        resource_type: AuditLogResourceType,
        tenant_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        resource_id: Optional[str] = None,
        description: Optional[str] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None,
    ) -> AuditLog:
        """
        Create a new audit log entry securely.
        """
        ip_address = None
        user_agent = None
        request_id = None
        
        if request:
            if request.client:
                ip_address = request.client.host
            user_agent = request.headers.get("user-agent")
            # If request ID middleware exists in future, it can be extracted here
            request_id = request.headers.get("x-request-id")

        log = AuditLog(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            description=description,
            old_values=_sanitize_data(old_values),
            new_values=_sanitize_data(new_values),
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )

        return await self._repo.create(log)

    async def get_audit_log(self, log_id: uuid.UUID, current_tenant_id: Optional[uuid.UUID] = None) -> AuditLogResponse:
        log = await self._repo.get_by_id(log_id)
        if not log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Audit log not found.",
            )
            
        # Tenant isolation
        if current_tenant_id and log.tenant_id != current_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied.",
            )
            
        return AuditLogResponse.model_validate(log)

    async def list_audit_logs(
        self,
        page: int,
        page_size: int,
        tenant_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        action: Optional[AuditLogAction] = None,
        resource_type: Optional[AuditLogResourceType] = None,
        resource_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        current_tenant_id: Optional[uuid.UUID] = None,
    ) -> AuditLogListResponse:
        
        # Enforce tenant isolation strictly
        if current_tenant_id:
            tenant_id = current_tenant_id

        items, total = await self._repo.list(
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
        total_pages = math.ceil(total / page_size) if total > 0 else 1
        
        return AuditLogListResponse(
            items=[AuditLogResponse.model_validate(item) for item in items],
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
