"""
Background job handlers (registered with `app.services.jobs`).

  INBOUND_MESSAGE         a channel message (WhatsApp) → full AI turn → reply delivery → post-reply analysis
  INDEX_DOCUMENT          embed a document's chunks
  INDEX_PRODUCT           (re)index one product for retrieval
  REINDEX_TENANT          re-embed chunks created by an outdated embedding model
  SUMMARIZE_CONVERSATION  refresh the rolling conversation summary (long-conversation memory)
"""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_assistant_config import AIAssistantConfig
from app.models.ai_job import AIJob
from app.models.conversation import Conversation, Message
from app.models.lead import Lead
from app.models.product import Product
from app.services.ai.llm import build_llm_client
from app.services.ai.summary import refresh_summary
from app.services.ai.tracing import TurnTrace, persist_trace
from app.services.ai.vector_store import sync_pgvector
from app.services.channels import InboundProcessor
from app.services.jobs import register_handler
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)


@register_handler("INBOUND_MESSAGE")
async def handle_inbound_message(db: AsyncSession, job: AIJob) -> None:
    p = job.payload
    await InboundProcessor.process(
        db,
        tenant_id=job.tenant_id,
        channel=p["channel"],
        phone=p["phone"],
        text=p["text"],
        customer_name=p.get("customer_name"),
        analysis_inline=False,
    )


@register_handler("INDEX_DOCUMENT")
async def handle_index_document(db: AsyncSession, job: AIJob) -> None:
    await RAGService.index_document_record(db, uuid.UUID(job.payload["document_id"]))


@register_handler("INDEX_PRODUCT")
async def handle_index_product(db: AsyncSession, job: AIJob) -> None:
    product = (
        await db.execute(select(Product).where(Product.id == uuid.UUID(job.payload["product_id"])))
    ).scalar_one_or_none()
    if product is not None:
        await RAGService.index_product(db, product)


@register_handler("REINDEX_TENANT")
async def handle_reindex_tenant(db: AsyncSession, job: AIJob) -> None:
    total = await RAGService.reindex_stale(db, job.tenant_id)
    synced = await sync_pgvector(db, job.tenant_id)
    await db.commit()
    logger.info("Re-embedded %d chunk(s) (pgvector rows synced: %d)", total, synced)


@register_handler("SUMMARIZE_CONVERSATION")
async def handle_summarize(db: AsyncSession, job: AIJob) -> None:
    conv = (
        await db.execute(select(Conversation).where(Conversation.id == uuid.UUID(job.payload["conversation_id"])))
    ).scalar_one_or_none()
    if conv is None:
        return
    config = (
        await db.execute(select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == conv.tenant_id))
    ).scalar_one_or_none()
    rows = (
        await db.execute(
            select(Message.sender_type, Message.content)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.asc())
        )
    ).all()
    new = [(r[0], r[1]) for r in rows[conv.summary_message_count or 0:]][-40:]
    if not new:
        return
    lead = (await db.execute(select(Lead).where(Lead.conversation_id == conv.id))).scalar_one_or_none()
    trace = TurnTrace(tenant_id=conv.tenant_id, conversation_id=conv.id, customer_id=conv.customer_id, trigger="SUMMARY")
    text, res = await refresh_summary(
        build_llm_client(config, task="fast"), previous=conv.ai_summary, messages=new, score=lead.score if lead else 0
    )
    if res is not None and text:
        conv.ai_summary = text
        conv.summary_message_count = len(rows)
        trace.add_llm(res, "SUMMARY", count_in_trace=False)
    await persist_trace(db, trace)
    await db.commit()
