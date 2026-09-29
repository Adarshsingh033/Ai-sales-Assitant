"""
Authentication service — orchestrates credential verification and JWT issuance.

Flow:
  Router → AuthService → UserRepository + security utilities → Token response
"""
import logging
from typing import Optional

from fastapi import HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, verify_password
from app.models.audit_log import AuditLogAction, AuditLogResourceType
from app.repositories.user_repository import UserRepository
from app.schemas.auth import AuthenticatedUser, LoginRequest, LoginResponse
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = UserRepository(db)

    async def login(self, login_req: LoginRequest, request: Optional[Request] = None) -> LoginResponse:
        """
        Authenticate a user by email and password.

        Generic error messages are used intentionally to prevent email enumeration.
        """
        email = login_req.email.lower().strip()

        # ------------------------------------------------------------------
        # 1. Lookup user
        # ------------------------------------------------------------------
        user = await self._repo.get_by_email(email)

        # ------------------------------------------------------------------
        # 2. Validate credentials — same error for "not found" and "wrong pw"
        # ------------------------------------------------------------------
        if user is None or not verify_password(login_req.password, user.password_hash):
            logger.info("Failed login attempt for email=%s", email)
            audit = AuditService(self._db)
            await audit.create_audit_log(
                action=AuditLogAction.LOGIN_FAILED,
                resource_type=AuditLogResourceType.AUTHENTICATION,
                description=f"Failed login attempt for email: {email}",
                request=request
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password.",
            )

        # ------------------------------------------------------------------
        # 3. Check account is active
        # ------------------------------------------------------------------
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is inactive. Please contact support.",
            )

        # ------------------------------------------------------------------
        # 4. Record last login
        # ------------------------------------------------------------------
        await self._repo.update_last_login(user.id)
        logger.info("Successful login for user_id=%s role=%s", user.id, user.role)
        
        audit = AuditService(self._db)
        await audit.create_audit_log(
            action=AuditLogAction.LOGIN,
            resource_type=AuditLogResourceType.AUTHENTICATION,
            user_id=user.id,
            tenant_id=user.tenant_id,
            description="User logged in successfully.",
            request=request
        )

        # ------------------------------------------------------------------
        # 5. Generate JWT
        # ------------------------------------------------------------------
        tenant_str: Optional[str] = str(user.tenant_id) if user.tenant_id else None
        token = create_access_token(
            subject=str(user.id),
            role=user.role.value,
            tenant_id=tenant_str,
        )

        # ------------------------------------------------------------------
        # 6. Build safe response (no password_hash, no internal fields)
        # ------------------------------------------------------------------
        return LoginResponse(
            access_token=token,
            token_type="bearer",
            user=AuthenticatedUser(
                id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                role=user.role.value,
                tenant_id=user.tenant_id,
            ),
        )
