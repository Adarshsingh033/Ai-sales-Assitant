"""
Meta WhatsApp Cloud API Webhook endpoints.
Handles the verification challenge and incoming messages (signature-verified, de-duplicated, queued).
"""
import json
import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.ai_assistant_config import AIAssistantConfig
from app.services.whatsapp_service import WhatsAppService, extract_messages, verify_signature

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
    """Handles Meta's verification challenge during webhook setup."""
    if hub_mode == "subscribe":
        if hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
            logger.info("WhatsApp webhook verified successfully with settings token!")
            return Response(content=hub_challenge, media_type="text/plain")

        stmt = select(AIAssistantConfig).where(AIAssistantConfig.whatsapp_verify_token == hub_verify_token)
        cfg = (await db.execute(stmt)).scalars().first()
        if cfg:
            logger.info("WhatsApp webhook verified successfully with tenant config token!")
            return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("WhatsApp webhook verification failed (token mismatch).")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification token mismatch.")


@router.post(
    "",
    summary="Receive WhatsApp Webhook Events",
    description="Receives customer messages from Meta. Verifies the signature, queues messages, replies 200 immediately.",
)
async def receive_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Dict[str, object]:
    raw = await request.body()
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload.")

    # The signing secret may be tenant-specific, so look the tenant up (read-only) before trusting the body.
    messages = extract_messages(payload)
    phone_id = messages[0]["phone_number_id"] if messages else None
    config = await WhatsAppService.find_config(db, phone_id) if phone_id else None
    secret = WhatsAppService.app_secret_for(config)
    if secret:
        if not verify_signature(raw, request.headers.get("X-Hub-Signature-256"), secret):
            logger.warning("WhatsApp webhook rejected: bad signature")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature.")
    elif settings.APP_ENV.lower() == "production":
        logger.error("WhatsApp webhook rejected: WHATSAPP_APP_SECRET is not configured in production")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook signature verification is not configured.")
    else:
        logger.warning("WhatsApp webhook accepted WITHOUT signature verification (no app secret configured).")

    result = await WhatsAppService.process_inbound_webhook(db, payload)
    return {"status": "EVENT_RECEIVED", **result}
