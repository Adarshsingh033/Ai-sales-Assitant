"""
Conversation Management & Human Handoff endpoints for Tenant Admin and Sales Users.
Provides real-time conversation viewing, human takeover, and agent replies.
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_sales_user
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.user import User
from app.schemas.conversation import (
    ConversationListResponse,
    ConversationResponse,
    HumanReplyRequest,
    MessageResponse,
)
from app.services.whatsapp_service import WhatsAppService

router = APIRouter(prefix="/tenant-admin/conversations", tags=["Tenant Admin Conversations"])


def _to_conv_response(c: Conversation) -> ConversationResponse:
    cust = c.customer
    last_msg = c.messages[-1].content if c.messages else None
    msgs_resp = [MessageResponse.model_validate(m) for m in c.messages] if c.messages else []
    return ConversationResponse(
        id=c.id,
        tenant_id=c.tenant_id,
        customer_id=c.customer_id,
        customer_name=cust.name if cust else None,
        customer_phone=cust.phone if cust else None,
        customer_email=cust.email if cust else None,
        channel=c.channel,
        status=c.status,
        assigned_user_id=c.assigned_user_id,
        ai_summary=c.ai_summary,
        detected_intent=c.detected_intent,
        last_message_preview=last_msg[:80] if last_msg else None,
        last_activity_at=c.last_activity_at,
        created_at=c.created_at,
        messages=msgs_resp,
    )


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List Conversations",
)
async def list_conversations(
    conv_status: Optional[str] = Query(None, alias="status"),
    channel: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ConversationListResponse:
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer), selectinload(Conversation.messages))
        .where(Conversation.tenant_id == tenant_id)
    )

    if conv_status:
        stmt = stmt.where(Conversation.status == conv_status.upper())

    if channel:
        stmt = stmt.where(Conversation.channel == channel.upper())

    stmt = stmt.order_by(Conversation.last_activity_at.desc()).limit(100)
    rows = (await db.execute(stmt)).scalars().all()

    return ConversationListResponse(
        items=[_to_conv_response(c) for c in rows],
        total=len(rows),
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get Conversation Details",
)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ConversationResponse:
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer), selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == current_user.tenant_id,
        )
    )
    conv = (await db.execute(stmt)).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return _to_conv_response(conv)


@router.get(
    "/{conversation_id}/messages",
    response_model=List[MessageResponse],
    summary="Get Conversation Message Thread",
)
async def get_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> List[MessageResponse]:
    stmt = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == current_user.tenant_id,
        )
        .order_by(Message.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    return [MessageResponse.model_validate(m) for m in rows]


@router.post(
    "/{conversation_id}/takeover",
    response_model=ConversationResponse,
    summary="Human Takeover of Conversation",
)
async def takeover_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ConversationResponse:
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer), selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == current_user.tenant_id,
        )
    )
    conv = (await db.execute(stmt)).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conv.status = "ESCALATED_HUMAN"
    conv.assigned_user_id = current_user.id
    await db.commit()
    await db.refresh(conv)

    return _to_conv_response(conv)


@router.post(
    "/{conversation_id}/reply",
    response_model=MessageResponse,
    summary="Send Human Agent Reply",
)
async def human_reply(
    conversation_id: uuid.UUID,
    data: HumanReplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> MessageResponse:
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer))
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == current_user.tenant_id,
        )
    )
    conv = (await db.execute(stmt)).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    # Mark as human escalated and assign to user
    conv.status = "ESCALATED_HUMAN"
    conv.assigned_user_id = current_user.id
    conv.last_activity_at = current_user.updated_at

    msg = Message(
        conversation_id=conv.id,
        sender_type="HUMAN_AGENT",
        content=data.content.strip(),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # If WhatsApp channel, dispatch to customer's phone
    if conv.channel == "WHATSAPP" and conv.customer and conv.customer.phone:
        stmt_cfg = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == current_user.tenant_id)
        config = (await db.execute(stmt_cfg)).scalar_one_or_none()
        phone_id = (config.whatsapp_phone_number_id if config else None) or settings.WHATSAPP_PHONE_NUMBER_ID
        token = (config.whatsapp_access_token if config else None) or settings.WHATSAPP_ACCESS_TOKEN
        if phone_id and token:
            await WhatsAppService.send_whatsapp_message(
                phone_number_id=phone_id,
                access_token=token,
                to_phone=conv.customer.phone,
                message_text=data.content.strip(),
            )
    # If Telegram channel, dispatch to customer on Telegram
    elif conv.channel == "TELEGRAM" and conv.customer and conv.customer.phone:
        from app.services.telegram_service import telegram_service
        chat_id = conv.customer.phone.replace("+tg_", "").replace("tg_", "")
        await telegram_service.send_message(chat_id=chat_id, text=data.content.strip())

    return MessageResponse.model_validate(msg)


@router.post(
    "/{conversation_id}/handback",
    response_model=ConversationResponse,
    summary="Handback Conversation to AI Assistant",
)
async def handback_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ConversationResponse:
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer), selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == current_user.tenant_id,
        )
    )
    conv = (await db.execute(stmt)).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conv.status = "ACTIVE_AI"
    await db.commit()
    await db.refresh(conv)

    return _to_conv_response(conv)


@router.post(
    "/{conversation_id}/resolve",
    response_model=ConversationResponse,
    summary="Resolve / Close Conversation",
)
async def resolve_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ConversationResponse:
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer), selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == current_user.tenant_id,
        )
    )
    conv = (await db.execute(stmt)).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    conv.status = "RESOLVED"
    await db.commit()
    await db.refresh(conv)

    return _to_conv_response(conv)
