import asyncio
from datetime import datetime, timezone
import json
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_sales_user
from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.user import User
from app.services.ai_sales_engine import AISalesEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant-admin/chat", tags=["Tenant Admin Chat Simulator"])


class ChatSimulateRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    customer_name: Optional[str] = "Demo Prospect"
    customer_phone: Optional[str] = "+15550001111"


class ChatSimulateResponse(BaseModel):
    session_id: str
    reply: str
    intent: Optional[str]
    score: int
    score_reason: Optional[str]
    needs_human_handoff: bool
    lead_id: Optional[str]
    human_takeover: Optional[bool] = False


class MessageItem(BaseModel):
    id: str
    sender_type: str
    content: str
    created_at: str


@router.post(
    "/simulate",
    response_model=ChatSimulateResponse,
    summary="Simulate Sales Conversation",
)
async def simulate_chat(
    data: ChatSimulateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ChatSimulateResponse:
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    # Find or create simulation customer
    cust_phone = data.customer_phone or "+15550001111"
    stmt_cust = select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.phone == cust_phone,
    )
    customer = (await db.execute(stmt_cust)).scalar_one_or_none()
    if not customer:
        customer = Customer(
            tenant_id=tenant_id,
            name=data.customer_name or "Demo Prospect",
            phone=cust_phone,
        )
        db.add(customer)
        await db.flush()
    elif data.customer_name and customer.name != data.customer_name:
        customer.name = data.customer_name
        db.add(customer)
        await db.flush()

    # Find or create simulation conversation
    conv_id = None
    if data.session_id:
        try:
            conv_id = uuid.UUID(data.session_id)
        except ValueError:
            pass

    conversation = None
    if conv_id:
        stmt_conv = select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.tenant_id == tenant_id,
        )
        conversation = (await db.execute(stmt_conv)).scalar_one_or_none()

    # If no specific session_id or session not found, reuse the customer's latest active conversation
    if not conversation:
        stmt_active = (
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.customer_id == customer.id,
                Conversation.channel == "WEB_SIMULATOR",
                Conversation.status.in_(["ACTIVE_AI", "WAITING_SALES", "HUMAN_AGENT", "ESCALATED_HUMAN"]),
            )
            .order_by(Conversation.last_activity_at.desc())
        )
        conversation = (await db.execute(stmt_active)).scalars().first()

    if not conversation:
        conversation = Conversation(
            tenant_id=tenant_id,
            customer_id=customer.id,
            channel="WEB_SIMULATOR",
            status="ACTIVE_AI",
        )
        db.add(conversation)
        await db.flush()

    # If human representative has taken over, pause AI and record message for human
    if conversation.status in ["ESCALATED_HUMAN", "HUMAN_AGENT"]:
        cust_msg = Message(
            conversation_id=conversation.id,
            sender_type="CUSTOMER",
            content=data.message.strip(),
        )
        db.add(cust_msg)
        conversation.last_activity_at = datetime.now(timezone.utc)
        await db.commit()

        return ChatSimulateResponse(
            session_id=str(conversation.id),
            reply="",
            intent="human_takeover",
            score=0,
            score_reason="Human representative is handling this conversation",
            needs_human_handoff=True,
            lead_id=None,
            human_takeover=True,
        )

    # Run AI Sales Engine
    res = await AISalesEngine.process_customer_message(
        db=db,
        tenant_id=tenant_id,
        customer_id=customer.id,
        conversation_id=conversation.id,
        user_message_text=data.message,
    )

    return ChatSimulateResponse(
        session_id=str(conversation.id),
        reply=res["reply"],
        intent=res["intent"],
        score=res["score"],
        score_reason=res["score_reason"],
        needs_human_handoff=res["needs_human_handoff"],
        lead_id=res["lead_id"],
    )


@router.post(
    "/simulate-stream",
    summary="Simulate Sales Conversation with SSE Streaming",
)
async def simulate_chat_stream(
    data: ChatSimulateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
):
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    # Find or create simulation customer
    cust_phone = data.customer_phone or "+15550001111"
    stmt_cust = select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.phone == cust_phone,
    )
    customer = (await db.execute(stmt_cust)).scalar_one_or_none()
    if not customer:
        customer = Customer(
            tenant_id=tenant_id,
            name=data.customer_name or "Demo Prospect",
            phone=cust_phone,
        )
        db.add(customer)
        await db.flush()
    elif data.customer_name and customer.name != data.customer_name:
        customer.name = data.customer_name
        db.add(customer)
        await db.flush()

    # Find or create simulation conversation
    conv_id = None
    if data.session_id:
        try:
            conv_id = uuid.UUID(data.session_id)
        except ValueError:
            pass

    conversation = None
    if conv_id:
        stmt_conv = select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.tenant_id == tenant_id,
        )
        conversation = (await db.execute(stmt_conv)).scalar_one_or_none()

    # If no specific session_id or session not found, reuse the customer's latest active conversation
    if not conversation:
        stmt_active = (
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.customer_id == customer.id,
                Conversation.channel == "WEB_SIMULATOR",
                Conversation.status.in_(["ACTIVE_AI", "WAITING_SALES", "HUMAN_AGENT", "ESCALATED_HUMAN"]),
            )
            .order_by(Conversation.last_activity_at.desc())
        )
        conversation = (await db.execute(stmt_active)).scalars().first()

    if not conversation:
        conversation = Conversation(
            tenant_id=tenant_id,
            customer_id=customer.id,
            channel="WEB_SIMULATOR",
            status="ACTIVE_AI",
        )
        db.add(conversation)
        await db.flush()

    # If human representative has taken over, pause AI Groq generation and save customer message
    if conversation.status in ["ESCALATED_HUMAN", "HUMAN_AGENT"]:
        cust_msg = Message(
            conversation_id=conversation.id,
            sender_type="CUSTOMER",
            content=data.message.strip(),
        )
        db.add(cust_msg)
        conversation.last_activity_at = datetime.now(timezone.utc)
        await db.commit()

        async def takeover_event_generator():
            payload = {
                "done": True,
                "session_id": str(conversation.id),
                "human_takeover": True,
                "reply": "",
                "score": 0,
                "score_reason": "Human representative active",
                "intent": "human_takeover",
                "needs_human_handoff": True,
                "lead_id": None,
            }
            yield f"data: {json.dumps(payload)}\n\n"

        return StreamingResponse(
            takeover_event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Prepare context, save customer message
    llm_messages, groq_client, config, conv = await AISalesEngine.prepare_chat_context(
        db=db,
        tenant_id=tenant_id,
        customer_id=customer.id,
        conversation_id=conversation.id,
        user_message_text=data.message,
    )

    async def event_generator():
        chunks = []
        try:
            async for token in groq_client.stream_chat_completion(llm_messages, temperature=0.3):
                chunks.append(token)
                yield f"data: {json.dumps({'token': token})}\n\n"
                await asyncio.sleep(0.015)
        except Exception as exc:
            logger.error("Groq stream chat failed: %s", exc)
            if not chunks:
                fallback = "Thank you for reaching out! Our sales team has received your message and will get back to you shortly."
                yield f"data: {json.dumps({'token': fallback})}\n\n"
                chunks.append(fallback)

        full_reply = "".join(chunks).strip()
        try:
            res = await AISalesEngine.finalize_ai_response(
                db=db,
                tenant_id=tenant_id,
                customer_id=customer.id,
                conversation_id=conversation.id,
                user_message_text=data.message,
                ai_reply=full_reply,
                groq_client=groq_client,
                config=config,
            )
            yield f"data: {json.dumps({'done': True, 'session_id': str(conversation.id), 'reply': full_reply, 'intent': res.get('intent'), 'score': res.get('score', 0), 'score_reason': res.get('score_reason'), 'needs_human_handoff': res.get('needs_human_handoff', False), 'lead_id': res.get('lead_id')})}\n\n"
        except Exception as exc:
            logger.error("Finalizing AI response in stream failed: %s", exc)
            yield f"data: {json.dumps({'done': True, 'session_id': str(conversation.id), 'reply': full_reply, 'intent': 'general_inquiry', 'score': 30, 'score_reason': 'Default', 'needs_human_handoff': False, 'lead_id': None})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )



@router.get(
    "/history/{session_id}",
    response_model=List[MessageItem],
    summary="Get Simulation Conversation History",
)
async def get_chat_history(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> List[MessageItem]:
    tenant_id = current_user.tenant_id
    stmt = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.id == session_id,
            Conversation.tenant_id == tenant_id,
        )
        .order_by(Message.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    return [
        MessageItem(
            id=str(m.id),
            sender_type=m.sender_type,
            content=m.content,
            created_at=m.created_at.isoformat(),
        )
        for m in rows
    ]


class ResetSessionRequest(BaseModel):
    customer_phone: Optional[str] = "+15550199001"


@router.post(
    "/reset-session",
    summary="Reset / Archive active simulation session",
)
async def reset_simulation_session(
    data: ResetSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
):
    tenant_id = current_user.tenant_id
    cust_phone = data.customer_phone or "+15550199001"
    stmt_cust = select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone == cust_phone)
    cust = (await db.execute(stmt_cust)).scalar_one_or_none()
    if cust:
        stmt_convs = select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.customer_id == cust.id,
            Conversation.status.in_(["ACTIVE_AI", "WAITING_SALES", "ESCALATED_HUMAN", "HUMAN_AGENT"]),
        )
        active_convs = (await db.execute(stmt_convs)).scalars().all()
        for cv in active_convs:
            cv.status = "RESOLVED"
        await db.commit()

    return {"status": "ok", "message": "Active simulation session archived."}

