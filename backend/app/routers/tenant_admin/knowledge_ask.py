"""
"Ask the knowledge base": a chat that gives the internal team short, source-backed answers from the company's documents
and product catalog (any Tenant Admin or Sales User), plus the admin list of questions it could not answer.
"""
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.database import get_db
from app.core.dependencies import CRMContext, TenantAdminContext, require_crm_context, require_tenant_admin_context
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.knowledge import KnowledgeDocument
from app.models.knowledge_gap import KnowledgeGap
from app.models.product import Product
from app.models.user import User
from app.schemas.knowledge_qa import (
    AskRequest,
    AskResponse,
    AskSource,
    GapItem,
    GapListResponse,
    LibraryDocument,
    LibraryResponse,
)
from app.services.ai import knowledge_qa, quota
from app.services.ai.llm import LLMError, build_llm_client
from app.services.ai_usage_tracker import AIUsageTracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant-admin/knowledge", tags=["Knowledge Base - Ask"])

_RATE_LIMIT = 20  # questions per minute per person: a stuck button must not burn the AI budget
_recent: Dict[uuid.UUID, Deque[float]] = defaultdict(deque)


def _rate_limit(user_id: uuid.UUID) -> None:
    now = time.monotonic()
    q = _recent[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= _RATE_LIMIT:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="You're asking very quickly. Please wait a moment.")
    q.append(now)


_LLM_MESSAGES = {
    "no_api_key": "The AI isn't set up yet. An admin needs to add the AI key under AI Assistant.",
    "auth": "The AI key was rejected. An admin needs to check it under AI Assistant.",
}


@router.post("/ask", response_model=AskResponse, summary="Ask a question; answered only from the company's knowledge")
async def ask(
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> AskResponse:
    _rate_limit(ctx.user.id)
    config = (
        await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == ctx.tenant_id))
    ).scalar_one_or_none()
    budget = await quota.check_quota(db, ctx.tenant_id, config)
    if budget["exceeded"]:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="This month's AI limit has been reached. Ask an admin to raise it.")

    try:
        result = await knowledge_qa.answer_question(
            db,
            tenant_id=ctx.tenant_id,
            question=body.question,
            history=[t.model_dump() for t in body.history],
            llm=build_llm_client(config, task="reply"),
            fast_llm=build_llm_client(config, task="fast"),
        )
    except LLMError as exc:
        logger.warning("Knowledge Q&A unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_LLM_MESSAGES.get(getattr(exc, "code", ""), "The AI is busy right now. Please try again in a moment."),
        )

    if result.prompt_tokens or result.completion_tokens:
        await AIUsageTracker.log_usage(
            db, ctx.tenant_id, result.model or "knowledge-qa", action_type="KNOWLEDGE_QA",
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
            latency_ms=result.latency_ms, user_id=ctx.user.id,
        )
        quota.note_usage(ctx.tenant_id, result.prompt_tokens + result.completion_tokens)
    if result.is_gap:
        await knowledge_qa.record_gap(db, ctx.tenant_id, ctx.user.id, body.question)
    elif result.status == "answered":
        await knowledge_qa.resolve_gap(db, ctx.tenant_id, body.question)
    await db.commit()

    return AskResponse(
        status=result.status,
        answer=result.answer,
        sources=[AskSource(n=p.n, kind=p.kind, title=p.title, location=p.location, text=p.text) for p in result.sources],
        searched_for=result.query if result.query != body.question else None,
    )


@router.get("/library", response_model=LibraryResponse, summary="What the assistant can answer from (read-only)")
async def library(
    db: AsyncSession = Depends(get_db),
    ctx: CRMContext = Depends(require_crm_context),
) -> LibraryResponse:
    docs = (
        await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.tenant_id == ctx.tenant_id, KnowledgeDocument.status == "INDEXED")
            .order_by(KnowledgeDocument.updated_at.desc())
        )
    ).scalars().all()
    product_filter = (Product.tenant_id == ctx.tenant_id, Product.is_active.is_(True))
    products = (await db.execute(select(func.count(Product.id)).where(*product_filter))).scalar_one()
    names = (await db.execute(select(Product.name).where(*product_filter).order_by(Product.name).limit(3))).scalars().all()
    return LibraryResponse(
        documents=[
            LibraryDocument(id=d.id, title=d.filename, file_type=d.file_type, status=d.status, updated_at=d.updated_at)
            for d in docs
        ],
        product_count=products,
        product_names=list(names),
    )


# ----------------------------------------------------------------------------- questions nobody could answer (admins)
def _gap_item(gap: KnowledgeGap, first, last) -> GapItem:
    who = f"{first or ''} {last or ''}".strip() or None
    return GapItem(id=gap.id, question=gap.question, times_asked=gap.times_asked, status=gap.status,
                   last_asked_by=who, last_asked_at=gap.last_asked_at)


@router.get("/gaps", response_model=GapListResponse, summary="Questions the knowledge base could not answer")
async def list_gaps(
    include_dismissed: bool = False,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> GapListResponse:
    asker = aliased(User)
    wanted = ["OPEN", "DISMISSED"] if include_dismissed else ["OPEN"]
    rows = (
        await db.execute(
            select(KnowledgeGap, asker.first_name, asker.last_name)
            .outerjoin(asker, KnowledgeGap.last_asked_by_id == asker.id)
            .where(KnowledgeGap.tenant_id == ctx.tenant_id, KnowledgeGap.status.in_(wanted))
            .order_by(KnowledgeGap.status.asc(), KnowledgeGap.times_asked.desc(), KnowledgeGap.last_asked_at.desc())
            .limit(100)
        )
    ).all()
    open_count = (
        await db.execute(
            select(func.count(KnowledgeGap.id)).where(KnowledgeGap.tenant_id == ctx.tenant_id, KnowledgeGap.status == "OPEN")
        )
    ).scalar_one()
    return GapListResponse(items=[_gap_item(*r) for r in rows], open_count=open_count)


@router.post("/gaps/{gap_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT, summary="Hide a question from the list")
async def dismiss_gap(
    gap_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: TenantAdminContext = Depends(require_tenant_admin_context),
) -> None:
    gap = (
        await db.execute(select(KnowledgeGap).where(KnowledgeGap.id == gap_id, KnowledgeGap.tenant_id == ctx.tenant_id))
    ).scalar_one_or_none()
    if gap is None:
        raise HTTPException(status_code=404, detail="Question not found.")
    gap.status = "DISMISSED"
    await db.commit()
