"""
Meta WhatsApp Cloud API Webhook endpoints.
Handles verification challenge and incoming WhatsApp messages.
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.services.whatsapp_service import WhatsAppService

from sqlalchemy import select
from app.models.ai_assistant_config import AIAssistantConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp Webhook"])


@router.get(
    "",
    summary="Meta Webhook Verification",
    description="Endpoint verified by Meta when configuring WhatsApp Cloud API webhook.",
)
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """
    Handles Meta's verification challenge during webhook setup.
    """
    if hub_mode == "subscribe":
        if hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
            logger.info("WhatsApp webhook verified successfully with settings token!")
            return Response(content=hub_challenge, media_type="text/plain")

        stmt = select(AIAssistantConfig).where(AIAssistantConfig.whatsapp_verify_token == hub_verify_token)
        cfg = (await db.execute(stmt)).scalar_one_or_none()
        if cfg:
            logger.info("WhatsApp webhook verified successfully with tenant config token!")
            return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("WhatsApp webhook verification failed. Token mismatch: %s", hub_verify_token)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch.",
    )


@router.post(
    "",
    summary="Receive WhatsApp Webhook Events",
    description="Receives incoming customer messages from Meta WhatsApp Cloud API.",
)
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    payload = await request.json()
    logger.info("Received WhatsApp webhook event")

    # Process message in background/async
    await WhatsAppService.process_inbound_webhook(db, payload)

    # Meta requires a fast 200 OK response
    return {"status": "EVENT_RECEIVED"}
