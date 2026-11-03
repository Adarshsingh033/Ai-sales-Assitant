"""
RAG Knowledge Service (v2).

Ingestion   extract → structure-aware chunking (stored immediately) → embedding (thread pool, resumable)
            statuses PENDING → PROCESSING → INDEXED | FAILED; re-uploads replace the previous version;
            identical content is de-duplicated by hash; stale-model chunks can be re-embedded.
Retrieval   dense (numpy / pgvector) + BM25 lexical, fused with Reciprocal Rank Fusion, tenant-scoped,
            returns a confidence score so the orchestrator can ask, answer or escalate.
Products    each active product is indexed as a PRODUCT chunk, so only relevant products reach the prompt.
"""
import asyncio
import hashlib
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.models.product import Product
from app.services.ai import chunking
from app.services.ai.embeddings import (
    QueryEmbeddingCache,
    embed_texts_sync,
    embedding_model_name,
    to_bytes,
)
from app.services.ai.lexical import correct_spelling, lexical_tokens
from app.services.ai.vector_store import (
    NumpyVectorStore,
    get_tenant_index,
    get_vector_store,
    invalidate_tenant_index,
)

logger = logging.getLogger(__name__)

_RRF_K = 60
_query_cache = QueryEmbeddingCache()


@dataclass
class RetrievalResult:
    query: str
    matches: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0  # best dense cosine among returned matches (0 when nothing relevant)
    backend: str = "numpy"
    latency_ms: int = 0


def product_text(p: Product) -> str:
    parts = [f"{p.name}"]
    if p.sku:
        parts[0] += f" (SKU: {p.sku})"
    meta = []
    if p.category:
        meta.append(f"Category: {p.category}")
    meta.append(f"Price: {p.currency} {float(p.price):.2f}")
    text = parts[0] + " | " + " | ".join(meta)
    if p.description:
        text += f"\n{p.description}"
    if p.features:
        text += f"\nFeatures: {p.features}"
    return text


class RAGService:
    # ------------------------------------------------------------------
    # Pure helpers (kept as static API for callers/tests)
    # ------------------------------------------------------------------
    @staticmethod
    def extract_text(file_bytes: bytes, file_type: str) -> str:
        """Extract plain text from uploaded file bytes based on file extension."""
        return chunking.blocks_to_text(chunking.extract_blocks(file_bytes, file_type))

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[str]:
        return chunking.chunk_plain_text(text, chunk_size=chunk_size, overlap=overlap)

    @staticmethod
    def generate_embeddings(texts: List[str]) -> List[List[float]]:
        """Dense embeddings from the configured provider (synchronous; call via a worker thread)."""
        return embed_texts_sync(texts)

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        if not len(v1) or not len(v2) or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        if n1 == 0.0 or n2 == 0.0:
            return 0.0
        return dot / (n1 * n2)

    @classmethod
    async def _embed(cls, texts: List[str]) -> List[List[float]]:
        return await asyncio.to_thread(cls.generate_embeddings, texts)

    @classmethod
    async def _embed_query(cls, text: str) -> np.ndarray:
        key = f"{embedding_model_name()}|{text}"
        cached = _query_cache.get(key)
        if cached is not None:
            return cached
        vec = np.asarray((await cls._embed([text]))[0], dtype=np.float32)
        _query_cache.put(key, vec)
        return vec

    @staticmethod
    def _embedding_input(title: str, section: Optional[str], content: str) -> str:
        header = f"{title} — {section}" if section else title
        return f"{header}\n{content}" if header else content

    @staticmethod
    def _title(filename: str) -> str:
        return filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------
    @classmethod
    async def create_document(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        filename: str,
        file_bytes: bytes,
        file_type: str,
    ) -> KnowledgeDocument:
        """
        Extract + chunk immediately (cheap, no model) and persist chunks without embeddings.
        Document stays PENDING until `index_document_record` embeds it. Duplicate content is not re-indexed.
        """
        blocks = await asyncio.to_thread(chunking.extract_blocks, file_bytes, file_type, filename)
        text = chunking.blocks_to_text(blocks)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        dup = (
            await db.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == tenant_id,
                    KnowledgeDocument.content_hash == content_hash,
                    KnowledgeDocument.status.in_(["INDEXED", "PENDING", "PROCESSING"]),
                )
            )
        ).scalars().first()
        if dup:
            return dup

        chunks = chunking.chunk_blocks(blocks)
        doc = KnowledgeDocument(
            tenant_id=tenant_id,
            filename=filename,
            file_type=file_type.lower().strip("."),
            file_size=len(file_bytes),
            status="PENDING",
            chunk_count=len(chunks),
            content_text=text,
            content_hash=content_hash,
        )
        db.add(doc)
        await db.flush()
        db.add_all(
            KnowledgeChunk(
                tenant_id=tenant_id, document_id=doc.id, source_type="DOCUMENT", chunk_index=i,
                content=c.content, section_title=(c.section or None) and c.section[:255], page_number=c.page,
                token_count=len(c.content.split()),
            )
            for i, c in enumerate(chunks)
        )
        await db.commit()
        await db.refresh(doc)
        return doc

    @classmethod
    async def index_document_record(cls, db: AsyncSession, document_id: uuid.UUID) -> KnowledgeDocument:
        """Embed every un-embedded chunk of a document and flip it to INDEXED (or FAILED with the reason)."""
        doc = (await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))).scalar_one()
        if doc.status == "INDEXED":
            return doc
        doc.status = "PROCESSING"
        doc.error_message = None
        await db.commit()
        try:
            await cls._embed_pending_chunks(db, doc)
            doc.status = "INDEXED"
            doc.embedding_model = embedding_model_name()
            await cls._retire_older_versions(db, doc)
        except Exception as exc:
            logger.exception("Indexing document %s failed", document_id)
            if db.dirty or db.new or db.deleted:  # embedding happens before any write, so usually nothing to undo
                await db.rollback()
            doc = (await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))).scalar_one()
            doc.status = "FAILED"
            doc.error_message = str(exc)[:500]
        await db.commit()
        invalidate_tenant_index(doc.tenant_id)
        await db.refresh(doc)
        return doc

    @classmethod
    async def _embed_pending_chunks(cls, db: AsyncSession, doc: KnowledgeDocument) -> None:
        rows = (
            await db.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == doc.id, KnowledgeChunk.embedding.is_(None))
                .order_by(KnowledgeChunk.chunk_index)
            )
        ).scalars().all()
        if not rows:
            return
        title = cls._title(doc.filename)
        vectors = await cls._embed([cls._embedding_input(title, r.section_title, r.content) for r in rows])
        model = embedding_model_name()
        for row, vec in zip(rows, vectors):
            row.embedding = to_bytes(vec)
            row.embedding_model = model
        await db.flush()
        await get_vector_store().upsert(db, [(r.id, r.tenant_id, r.source_type, v) for r, v in zip(rows, vectors)])

    @classmethod
    async def _retire_older_versions(cls, db: AsyncSession, doc: KnowledgeDocument) -> None:
        """A re-uploaded file with the same name supersedes older versions (their chunks cascade away)."""
        older = (
            await db.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == doc.tenant_id,
                    KnowledgeDocument.filename == doc.filename,
                    KnowledgeDocument.id != doc.id,
                    KnowledgeDocument.created_at < doc.created_at,
                )
            )
        ).scalars().all()
        if older:
            doc.version = max(d.version for d in older) + 1
            for d in older:
                await db.delete(d)

    @classmethod
    async def index_document(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        filename: str,
        file_bytes: bytes,
        file_type: str,
    ) -> KnowledgeDocument:
        """Create + embed synchronously (used for small uploads and tests)."""
        doc = await cls.create_document(db, tenant_id, filename, file_bytes, file_type)
        if doc.status in ("INDEXED", "PROCESSING"):
            return doc
        return await cls.index_document_record(db, doc.id)

    @classmethod
    async def reindex_document(cls, db: AsyncSession, document_id: uuid.UUID) -> KnowledgeDocument:
        """Rebuild chunks from the stored text with the current chunker and embed them again."""
        doc = (await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id))).scalar_one()
        text = doc.content_text or ""
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))
        chunks = chunking.chunk_blocks(chunking._extract_text(text)) if text else []
        db.add_all(
            KnowledgeChunk(
                tenant_id=doc.tenant_id, document_id=doc.id, source_type="DOCUMENT", chunk_index=i,
                content=c.content, section_title=c.section, page_number=c.page, token_count=len(c.content.split()),
            )
            for i, c in enumerate(chunks)
        )
        doc.chunk_count = len(chunks)
        doc.status = "PENDING"
        await db.commit()
        invalidate_tenant_index(doc.tenant_id)
        return await cls.index_document_record(db, doc.id)

    @classmethod
    async def delete_document(cls, db: AsyncSession, doc: KnowledgeDocument) -> None:
        tenant_id = doc.tenant_id
        await db.delete(doc)
        await db.commit()
        invalidate_tenant_index(tenant_id)

    # -- products ------------------------------------------------------
    @classmethod
    async def index_product(cls, db: AsyncSession, product: Product) -> None:
        """(Re)index one product; inactive products are removed from retrieval."""
        await db.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.tenant_id == product.tenant_id, KnowledgeChunk.product_id == product.id
            )
        )
        if product.is_active:
            text = product_text(product)
            vec = (await cls._embed([text]))[0]
            chunk = KnowledgeChunk(
                tenant_id=product.tenant_id, product_id=product.id, source_type="PRODUCT", chunk_index=0,
                content=text, section_title=product.category, token_count=len(text.split()),
                embedding=to_bytes(vec), embedding_model=embedding_model_name(),
            )
            db.add(chunk)
            await db.flush()
            await get_vector_store().upsert(db, [(chunk.id, chunk.tenant_id, "PRODUCT", vec)])
        await db.commit()
        invalidate_tenant_index(product.tenant_id)

    @classmethod
    async def remove_product(cls, db: AsyncSession, tenant_id: uuid.UUID, product_id: uuid.UUID) -> None:
        await db.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.tenant_id == tenant_id, KnowledgeChunk.product_id == product_id)
        )
        await db.commit()
        invalidate_tenant_index(tenant_id)

    # -- re-embedding ---------------------------------------------------
    @classmethod
    async def reindex_stale(cls, db: AsyncSession, tenant_id: Optional[uuid.UUID] = None, batch: int = 64) -> int:
        """Re-embed chunks produced by a different (older) embedding model. Returns chunks updated."""
        model = embedding_model_name()
        total = 0
        while True:
            stmt = (
                select(KnowledgeChunk, KnowledgeDocument.filename)
                .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id, isouter=True)
                .where(or_(KnowledgeChunk.embedding_model.is_(None), KnowledgeChunk.embedding_model != model))
                .where(KnowledgeChunk.content != "")
                .limit(batch)
            )
            if tenant_id:
                stmt = stmt.where(KnowledgeChunk.tenant_id == tenant_id)
            rows = (await db.execute(stmt)).all()
            if not rows:
                break
            inputs = [
                cls._embedding_input(cls._title(fn) if fn else "", ch.section_title if fn else None, ch.content)
                for ch, fn in rows
            ]
            vectors = await cls._embed(inputs)
            touched = set()
            for (chunk, _), vec in zip(rows, vectors):
                chunk.embedding = to_bytes(vec)
                chunk.embedding_model = model
                touched.add(chunk.tenant_id)
            await db.flush()
            await get_vector_store().upsert(
                db, [(c.id, c.tenant_id, c.source_type, v) for (c, _), v in zip(rows, vectors)]
            )
            await db.commit()
            for t in touched:
                invalidate_tenant_index(t)
            total += len(rows)
        if total:
            await db.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.status == "INDEXED")
                .values(embedding_model=model)
            )
            await db.commit()
        return total

    @classmethod
    async def count_stale(cls, db: AsyncSession, tenant_id: Optional[uuid.UUID] = None) -> int:
        from sqlalchemy import func

        stmt = select(func.count(KnowledgeChunk.id)).where(
            or_(KnowledgeChunk.embedding_model.is_(None), KnowledgeChunk.embedding_model != embedding_model_name())
        )
        if tenant_id:
            stmt = stmt.where(KnowledgeChunk.tenant_id == tenant_id)
        return int((await db.execute(stmt)).scalar_one() or 0)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    @classmethod
    async def correct_query(cls, db: AsyncSession, tenant_id: uuid.UUID, query: str) -> str:
        """`query` with words this tenant's knowledge has never seen replaced by their closest known spelling."""
        q = (query or "").strip()
        index = await get_tenant_index(db, tenant_id, need_vectors=get_vector_store().needs_blobs)
        return correct_spelling(q, index.bm25.postings) if q and index.chunks else q

    @classmethod
    async def retrieve(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        *,
        top_k: int = 4,
        min_score: Optional[float] = None,
        source_type: Optional[str] = None,
        fetch_k: int = 20,
    ) -> RetrievalResult:
        started = time.monotonic()
        q = (query or "").strip()
        store = get_vector_store()
        result = RetrievalResult(query=q, backend=store.name)
        if not q:
            return result
        min_score = settings.RAG_MIN_SCORE if min_score is None else min_score

        index = await get_tenant_index(db, tenant_id, need_vectors=store.needs_blobs)
        if not index.chunks:
            return result
        q = result.query = correct_spelling(q, index.bm25.postings)
        qvec = await cls._embed_query(q)

        try:
            dense = await store.search(db, tenant_id, index, qvec, fetch_k, source_type)
        except Exception as exc:  # accelerator failure must never take retrieval down
            logger.warning("%s search failed (%s); falling back to numpy", store.name, exc)
            index = await get_tenant_index(db, tenant_id, need_vectors=True)
            dense = await NumpyVectorStore().search(db, tenant_id, index, qvec, fetch_k, source_type)
            result.backend = "numpy"

        q_terms = set(lexical_tokens(q))
        lexical = [
            (pos, score)
            for pos, score in index.bm25.search(list(q_terms), fetch_k * 2)
            if not source_type or index.chunks[pos].source_type == source_type
        ][:fetch_k]

        cosine = {pos: s for pos, s in dense}
        fused: Dict[int, float] = {}
        for rank, (pos, _) in enumerate(dense):
            fused[pos] = fused.get(pos, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, (pos, _) in enumerate(lexical):
            fused[pos] = fused.get(pos, 0.0) + 1.0 / (_RRF_K + rank + 1)

        # A lexical-only hit (weak/unknown embedding similarity) needs to cover most of the query terms,
        # e.g. an exact SKU or product name; this is independent of corpus size, unlike a raw BM25 score.
        lexical_strong = {pos for pos, _ in lexical[:3] if index.bm25.coverage(pos, q_terms) >= 0.6}
        selected = [
            pos for pos in sorted(fused, key=fused.get, reverse=True)
            if cosine.get(pos, 0.0) >= min_score or pos in lexical_strong
        ][:top_k]

        for pos in selected:
            c = index.chunks[pos]
            result.matches.append(
                {
                    "chunk_id": c.id,
                    "filename": c.filename or (c.section or "Product catalog"),
                    "content": c.content,
                    "score": round(float(cosine.get(pos, 0.0)), 4),
                    "fused_score": round(fused[pos], 5),
                    "section": c.section,
                    "page": c.page,
                    "source_type": c.source_type,
                    "product_id": c.product_id,
                }
            )
        result.confidence = max((m["score"] for m in result.matches), default=0.0)
        result.latency_ms = int((time.monotonic() - started) * 1000)
        return result

    @classmethod
    async def search_knowledge(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        top_k: int = 4,
        similarity_threshold: Optional[float] = None,
        source_type: Optional[str] = "DOCUMENT",
    ) -> List[Dict[str, Any]]:
        """Search tenant knowledge (documents by default). Returns best matches first."""
        res = await cls.retrieve(
            db, tenant_id, query, top_k=top_k, min_score=similarity_threshold, source_type=source_type
        )
        return res.matches
