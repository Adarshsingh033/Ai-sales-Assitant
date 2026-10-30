"""
AI Assistant Configuration endpoints for Tenant Admin.
Allows configuring assistant persona, greeting, instructions, thresholds, follow-up policy and channel credentials.
Secrets are encrypted at rest and never returned.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.ai_config import (
    AIAssistantConfigResponse,
    AIAssistantConfigUpdate,
)
from app.services.telegram_service import telegram_service

router = APIRouter(prefix="/tenant-admin/ai-config", tags=["Tenant Admin AI Config"])

_SECRET_FIELDS = ("groq_api_key", "whatsapp_access_token", "whatsapp_app_secret")


def _to_response(config: AIAssistantConfig, telegram_link: Optional[str] = None) -> AIAssistantConfigResponse:
    return AIAssistantConfigResponse(
        id=config.id,
        tenant_id=config.tenant_id,
        assistant_name=config.assistant_name,
        personality=config.personality,
        custom_instructions=config.custom_instructions,
        greeting_message=config.greeting_message,
        language=config.language,
        max_discount_percent=config.max_discount_percent,
        qualification_threshold=config.qualification_threshold,
        groq_model=config.groq_model,
        fast_model=config.fast_model,
        whatsapp_phone_number_id=config.whatsapp_phone_number_id,
        whatsapp_verify_token=config.whatsapp_verify_token,
        whatsapp_followup_template=config.whatsapp_followup_template,
        is_enabled=config.is_enabled,
        auto_followup_enabled=config.auto_followup_enabled,
        followup_delay_hours=config.followup_delay_hours,
        max_followups=config.max_followups,
        followup_mode=config.followup_mode,
        round_robin_assignment=config.round_robin_assignment,
        notification_sound_enabled=config.notification_sound_enabled,
        monthly_token_limit=config.monthly_token_limit,
        has_groq_api_key=bool(config.groq_api_key),
        has_whatsapp_access_token=bool(config.whatsapp_access_token),
        has_whatsapp_app_secret=bool(config.whatsapp_app_secret),
        telegram_start_link=telegram_link,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


async def _telegram_link(db: AsyncSession, tenant_id) -> Optional[str]:
    slug = (await db.execute(select(Tenant.slug).where(Tenant.id == tenant_id))).scalar_one_or_none()
    return telegram_service.start_link(slug) if slug else None


@router.get("", response_model=AIAssistantConfigResponse, summary="Get AI Assistant Config")
async def get_ai_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> AIAssistantConfigResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant ID is required.")

    config = (
        await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == current_user.tenant_id))
    ).scalar_one_or_none()
    if not config:  # initialise defaults for the tenant
        config = AIAssistantConfig(tenant_id=current_user.tenant_id)
        db.add(config)
        await db.commit()
        await db.refresh(config)

    return _to_response(config, await _telegram_link(db, current_user.tenant_id))


@router.put("", response_model=AIAssistantConfigResponse, summary="Update AI Assistant Config")
async def update_ai_config(
    data: AIAssistantConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> AIAssistantConfigResponse:
    if not current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant ID is required.")

    config = (
        await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == current_user.tenant_id))
    ).scalar_one_or_none()
    if not config:
        config = AIAssistantConfig(tenant_id=current_user.tenant_id)
        db.add(config)

    for field, val in data.model_dump(exclude_unset=True).items():
        if field in _SECRET_FIELDS:
            # Never overwrite a stored secret with an empty value or a browser password autofill
            if not val or not str(val).strip():
                continue
            val = str(val).strip()
            if field == "groq_api_key" and "groq.com" in settings.LLM_BASE_URL and not val.startswith("gsk_"):
                continue
            val = encrypt_secret(val)
        setattr(config, field, val)

    await db.commit()
    await db.refresh(config)
    return _to_response(config, await _telegram_link(db, current_user.tenant_id))
