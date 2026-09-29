"""
Authentication service — orchestrates credential verification and JWT issuance.

Flow:
  Router → AuthService → UserRepository + security utilities → Token response
"""
import logging
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, verify_password
from app.repositories.user_repository import UserRepository
from app.schemas.auth import AuthenticatedUser, LoginRequest, LoginResponse

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = UserRepository(db)

    async def login(self, request: LoginRequest) -> LoginResponse:
        """
        Authenticate a user by email and password.

        Generic error messages are used intentionally to prevent email enumeration.
        """
        email = request.email.lower().strip()

        # ------------------------------------------------------------------
        # 1. Lookup user
        # ------------------------------------------------------------------
        user = await self._repo.get_by_email(email)

        # ------------------------------------------------------------------
        # 2. Validate credentials — same error for "not found" and "wrong pw"
        # ------------------------------------------------------------------
        if user is None or not verify_password(request.password, user.password_hash):
            logger.info("Failed login attempt for email=%s", email)
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
