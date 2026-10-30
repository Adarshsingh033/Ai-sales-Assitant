"""
AI Assistant Configuration endpoints for Tenant Admin.
Allows configuring assistant persona, greeting, instructions, thresholds, and channel credentials.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.user import User
from app.schemas.ai_config import (
    AIAssistantConfigResponse,
    AIAssistantConfigUpdate,
)

router = APIRouter(prefix="/tenant-admin/ai-config", tags=["Tenant Admin AI Config"])


def _to_response(config: AIAssistantConfig) -> AIAssistantConfigResponse:
    resp = AIAssistantConfigResponse(
        id=config.id,
        tenant_id=config.tenant_id,
        assistant_name=config.assistant_name,
        personality=config.personality,
        custom_instructions=config.custom_instructions,
        greeting_message=config.greeting_message,
        max_discount_percent=config.max_discount_percent,
        qualification_threshold=config.qualification_threshold,
        groq_model=config.groq_model,
        whatsapp_phone_number_id=config.whatsapp_phone_number_id,
        whatsapp_verify_token=config.whatsapp_verify_token,
        is_enabled=config.is_enabled,
        auto_followup_enabled=config.auto_followup_enabled,
        followup_delay_hours=config.followup_delay_hours,
        round_robin_assignment=config.round_robin_assignment,
        notification_sound_enabled=config.notification_sound_enabled,
        has_groq_api_key=bool(config.groq_api_key),
        has_whatsapp_access_token=bool(config.whatsapp_access_token),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )
    return resp


@router.get(
    "",
    response_model=AIAssistantConfigResponse,
    summary="Get AI Assistant Config",
)
async def get_ai_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> AIAssistantConfigResponse:
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID is required.",
        )

    stmt = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == current_user.tenant_id)
    config = (await db.execute(stmt)).scalar_one_or_none()

    if not config:
        # Initialize default config for tenant
        config = AIAssistantConfig(tenant_id=current_user.tenant_id)
        db.add(config)
        await db.commit()
        await db.refresh(config)

    return _to_response(config)


@router.put(
    "",
    response_model=AIAssistantConfigResponse,
    summary="Update AI Assistant Config",
)
async def update_ai_config(
    data: AIAssistantConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> AIAssistantConfigResponse:
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID is required.",
        )

    stmt = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == current_user.tenant_id)
    config = (await db.execute(stmt)).scalar_one_or_none()

    if not config:
        config = AIAssistantConfig(tenant_id=current_user.tenant_id)
        db.add(config)

    update_dict = data.model_dump(exclude_unset=True)

    # Don't overwrite api keys with empty strings or invalid password autofills
    for field, val in update_dict.items():
        if field == "groq_api_key":
            if not val or not str(val).strip().startswith("gsk_"):
                continue
            val = str(val).strip()
        elif field == "whatsapp_access_token" and val == "":
            continue
        setattr(config, field, val)

    await db.commit()
    await db.refresh(config)
    return _to_response(config)
