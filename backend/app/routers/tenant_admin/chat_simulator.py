import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
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

_HUMAN_STATES = ("ESCALATED_HUMAN", "HUMAN_AGENT")
_OPEN_STATES = ["ACTIVE_AI", "WAITING_SALES", "HUMAN_AGENT", "ESCALATED_HUMAN"]


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


async def _resolve_session(
    db: AsyncSession, tenant_id: uuid.UUID, data: ChatSimulateRequest
) -> Tuple[Customer, Conversation]:
    """Find or create the simulation customer and their (reusable) web-simulator conversation."""
    cust_phone = data.customer_phone or "+15550001111"
    customer = (
        await db.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone == cust_phone))
    ).scalar_one_or_none()
    if not customer:
        customer = Customer(tenant_id=tenant_id, name=data.customer_name or "Demo Prospect", phone=cust_phone)
        db.add(customer)
        await db.flush()
    elif data.customer_name and customer.name != data.customer_name:
        customer.name = data.customer_name
        await db.flush()

    conversation = None
    if data.session_id:
        try:
            conv_id = uuid.UUID(data.session_id)
            conversation = (
                await db.execute(
                    select(Conversation).where(Conversation.id == conv_id, Conversation.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
        except ValueError:
            pass

    if not conversation:  # reuse the customer's latest open simulation conversation
        conversation = (
            await db.execute(
                select(Conversation)
                .where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.customer_id == customer.id,
                    Conversation.channel == "WEB_SIMULATOR",
                    Conversation.status.in_(_OPEN_STATES),
                )
                .order_by(Conversation.last_activity_at.desc())
            )
        ).scalars().first()

    if not conversation:
        conversation = Conversation(
            tenant_id=tenant_id, customer_id=customer.id, channel="WEB_SIMULATOR", status="ACTIVE_AI"
        )
        db.add(conversation)
        await db.flush()
    return customer, conversation


async def _store_for_human(db: AsyncSession, conversation: Conversation, text: str) -> None:
    """A human rep owns the conversation: record the customer's message and keep the AI silent."""
    db.add(Message(conversation_id=conversation.id, sender_type="CUSTOMER", content=text.strip()))
    conversation.last_activity_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/simulate", response_model=ChatSimulateResponse, summary="Simulate Sales Conversation")
async def simulate_chat(
    data: ChatSimulateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> ChatSimulateResponse:
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    customer, conversation = await _resolve_session(db, tenant_id, data)

    if conversation.status in _HUMAN_STATES:
        await _store_for_human(db, conversation, data.message)
        return ChatSimulateResponse(
            session_id=str(conversation.id), reply="", intent="human_takeover", score=0,
            score_reason="Human representative is handling this conversation",
            needs_human_handoff=True, lead_id=None, human_takeover=True,
        )

    res = await AISalesEngine.process_customer_message(
        db=db, tenant_id=tenant_id, customer_id=customer.id,
        conversation_id=conversation.id, user_message_text=data.message, trigger="SIMULATOR",
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


_SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


@router.post("/simulate-stream", summary="Simulate Sales Conversation with SSE Streaming")
async def simulate_chat_stream(
    data: ChatSimulateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
):
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID is required.")

    customer, conversation = await _resolve_session(db, tenant_id, data)

    if conversation.status in _HUMAN_STATES:
        await _store_for_human(db, conversation, data.message)

        async def takeover_event_generator():
            payload = {
                "done": True, "session_id": str(conversation.id), "human_takeover": True, "reply": "",
                "score": 0, "score_reason": "Human representative active", "intent": "human_takeover",
                "needs_human_handoff": True, "lead_id": None,
            }
            yield f"data: {json.dumps(payload)}\n\n"

        return StreamingResponse(takeover_event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)

    async def event_generator():
        async for event in AISalesEngine.stream_message(
            db, tenant_id, customer.id, conversation.id, data.message
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/history/{session_id}", response_model=List[MessageItem], summary="Get Simulation Conversation History")
async def get_chat_history(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
) -> List[MessageItem]:
    tenant_id = current_user.tenant_id
    stmt = (
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.id == session_id, Conversation.tenant_id == tenant_id)
        .order_by(Message.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        MessageItem(id=str(m.id), sender_type=m.sender_type, content=m.content, created_at=m.created_at.isoformat())
        for m in rows
    ]


class ResetSessionRequest(BaseModel):
    customer_phone: Optional[str] = "+15550199001"


@router.post("/reset-session", summary="Reset / Archive active simulation session")
async def reset_simulation_session(
    data: ResetSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_sales_user),
):
    tenant_id = current_user.tenant_id
    cust_phone = data.customer_phone or "+15550199001"
    cust = (
        await db.execute(select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone == cust_phone))
    ).scalar_one_or_none()
    if cust:
        active_convs = (
            await db.execute(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.customer_id == cust.id,
                    Conversation.status.in_(["ACTIVE_AI", "WAITING_SALES", "ESCALATED_HUMAN", "HUMAN_AGENT"]),
                )
            )
        ).scalars().all()
        for cv in active_convs:
            cv.status = "RESOLVED"
        await db.commit()

    return {"status": "ok", "message": "Active simulation session archived."}
