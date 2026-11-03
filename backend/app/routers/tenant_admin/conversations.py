"""
Conversation Management & Human Handoff endpoints for Tenant Admin and Sales Users.
Provides real-time conversation viewing, human takeover, and agent replies.

A Sales User only sees (and can act on) conversations assigned to them; a Tenant Admin sees every conversation.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import CRMContext, require_crm_context
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.conversation import Conversation, Message
from app.models.lead import Lead
from app.schemas.conversation import (
    ConversationListResponse,
    ConversationResponse,
    HumanReplyRequest,
    MessageResponse,
)
from app.services import crm
from app.services.channels import ChannelSender

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


async def _reload(db: AsyncSession, conversation_id: uuid.UUID, tenant_id: uuid.UUID) -> Conversation:
    """Re-read a conversation with its customer and messages, whatever the session already holds."""
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer), selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.tenant_id == tenant_id)
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalar_one()


async def _get_conversation(db: AsyncSession, ctx: CRMContext, conversation_id: uuid.UUID, *, messages: bool = True) -> Conversation:
    options = [selectinload(Conversation.customer)]
    if messages:
        options.append(selectinload(Conversation.messages))
    conv = (
        await db.execute(
            select(Conversation).options(*options).where(*crm.conversation_scope(ctx), Conversation.id == conversation_id)
        )
    ).scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conv


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List Conversations",
)
async def list_conversations(
    conv_status: Optional[str] = Query(None, alias="status"),
    channel: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ConversationListResponse:
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.customer), selectinload(Conversation.messages))
        .where(*crm.conversation_scope(ctx))
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
    ctx: CRMContext = Depends(require_crm_context),
) -> ConversationResponse:
    conv = await _get_conversation(db, ctx, conversation_id)
    response = _to_conv_response(conv)
    response.lead_id = (
        await db.execute(
            select(Lead.id)
            .where(Lead.tenant_id == ctx.tenant_id, Lead.conversation_id == conv.id)
            .order_by(Lead.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return response


@router.get(
    "/{conversation_id}/messages",
    response_model=List[MessageResponse],
    summary="Get Conversation Message Thread",
)
async def get_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> List[MessageResponse]:
    await _get_conversation(db, ctx, conversation_id, messages=False)
    rows = (
        await db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
        )
    ).scalars().all()

    return [MessageResponse.model_validate(m) for m in rows]


@router.post(
    "/{conversation_id}/takeover",
    response_model=ConversationResponse,
    summary="Human Takeover of Conversation",
)
async def takeover_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ConversationResponse:
    conv = await _get_conversation(db, ctx, conversation_id)
    if conv.status != "ESCALATED_HUMAN" or conv.assigned_user_id != ctx.user.id:
        crm.log_activity(db, tenant_id=ctx.tenant_id, kind="CHAT", text=f"{crm.full_name(ctx.user)} took over the chat",
                         customer_id=conv.customer_id, actor_user_id=ctx.user.id)
    conv.status = "ESCALATED_HUMAN"
    conv.assigned_user_id = ctx.user.id
    await db.commit()

    return _to_conv_response(await _reload(db, conv.id, ctx.tenant_id))


@router.post(
    "/{conversation_id}/reply",
    response_model=MessageResponse,
    summary="Send Human Agent Reply",
)
async def human_reply(
    conversation_id: uuid.UUID,
    data: HumanReplyRequest,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> MessageResponse:
    conv = await _get_conversation(db, ctx, conversation_id, messages=False)

    # Mark as human escalated and assign to user
    conv.status = "ESCALATED_HUMAN"
    conv.assigned_user_id = ctx.user.id
    conv.last_activity_at = datetime.now(timezone.utc)

    msg = Message(
        conversation_id=conv.id,
        sender_type="HUMAN_AGENT",
        content=data.content.strip(),
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Deliver on the customer's channel (WhatsApp / Telegram); the web simulator needs no delivery
    if conv.customer:
        config = (
            await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == ctx.tenant_id))
        ).scalar_one_or_none()
        await ChannelSender.send(db, conv, conv.customer, config, data.content.strip())

    return MessageResponse.model_validate(msg)


@router.post(
    "/{conversation_id}/handback",
    response_model=ConversationResponse,
    summary="Handback Conversation to AI Assistant",
)
async def handback_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ConversationResponse:
    conv = await _get_conversation(db, ctx, conversation_id)
    conv.status = "ACTIVE_AI"
    await db.commit()

    return _to_conv_response(await _reload(db, conv.id, ctx.tenant_id))


@router.post(
    "/{conversation_id}/resolve",
    response_model=ConversationResponse,
    summary="Resolve / Close Conversation",
)
async def resolve_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> ConversationResponse:
    conv = await _get_conversation(db, ctx, conversation_id)
    conv.status = "RESOLVED"
    await db.commit()

    return _to_conv_response(await _reload(db, conv.id, ctx.tenant_id))
