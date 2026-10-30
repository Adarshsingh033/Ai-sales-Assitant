"""
Vector storage & dense search.

Embeddings live canonically in `knowledge_chunks.embedding` (float32 bytes). Two interchangeable search
backends sit on top of that:

  * NumpyVectorStore  — per-tenant cached, L2-normalised matrix; brute-force cosine via one matmul.
                        Portable (SQLite tests / PostgreSQL without pgvector), fast to ~100k chunks per tenant.
  * PgVectorStore     — sidecar table `knowledge_vectors` with an HNSW index (pgvector extension).
                        Chosen automatically when the extension is installed (VECTOR_BACKEND=auto|pgvector).

`init_vector_backend()` decides once at startup; any pgvector failure at query time transparently falls back
to the numpy path, so retrieval never goes down because of the accelerator.
"""
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import settings
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services.ai.embeddings import embedding_model_name, from_bytes, normalize
from app.services.ai.lexical import BM25Index, lexical_tokens

logger = logging.getLogger(__name__)

_MAX_CACHED_TENANTS = 32


# ---------------------------------------------------------------------------
# Tenant index (metadata + BM25 + optional dense matrix), cached and version-checked
# ---------------------------------------------------------------------------
@dataclass
class IndexedChunk:
    id: str
    content: str
    source_type: str
    filename: Optional[str] = None
    section: Optional[str] = None
    page: Optional[int] = None
    document_id: Optional[str] = None
    product_id: Optional[str] = None
    has_vector: bool = False


@dataclass
class TenantIndex:
    token: Tuple
    chunks: List[IndexedChunk]
    bm25: BM25Index
    id_pos: Dict[str, int] = field(default_factory=dict)
    matrix: Optional[np.ndarray] = None  # (m, dim) normalised, only rows that have a current-model vector
    row_pos: Optional[np.ndarray] = None  # matrix row -> position in `chunks`
    row_source: Optional[np.ndarray] = None  # matrix row -> source_type (object array)
    has_blobs: bool = False


_index_cache: "OrderedDict[str, TenantIndex]" = OrderedDict()


def invalidate_tenant_index(tenant_id: Optional[uuid.UUID] = None) -> None:
    if tenant_id is None:
        _index_cache.clear()
    else:
        _index_cache.pop(str(tenant_id), None)


async def _version_token(db: AsyncSession, tenant_id: uuid.UUID) -> Tuple:
    current = embedding_model_name()
    row = (
        await db.execute(
            select(
                func.count(KnowledgeChunk.id),
                func.max(KnowledgeChunk.created_at),
                func.sum(case((KnowledgeChunk.embedding_model == current, 1), else_=0)),
            ).where(KnowledgeChunk.tenant_id == tenant_id)
        )
    ).one()
    return (int(row[0] or 0), str(row[1]), int(row[2] or 0), current)


async def get_tenant_index(db: AsyncSession, tenant_id: uuid.UUID, *, need_vectors: bool) -> TenantIndex:
    token = await _version_token(db, tenant_id)
    key = str(tenant_id)
    cached = _index_cache.get(key)
    if cached and cached.token == token and (not need_vectors or cached.has_blobs):
        _index_cache.move_to_end(key)
        return cached

    current = embedding_model_name()
    cols = [
        KnowledgeChunk.id, KnowledgeChunk.content, KnowledgeChunk.source_type, KnowledgeChunk.section_title,
        KnowledgeChunk.page_number, KnowledgeChunk.document_id, KnowledgeChunk.product_id,
        KnowledgeChunk.embedding_model, KnowledgeDocument.filename,
    ]
    if need_vectors:
        cols.append(KnowledgeChunk.embedding)
    stmt = (
        select(*cols)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id, isouter=True)
        .where(KnowledgeChunk.tenant_id == tenant_id)
        # chunks of documents that are still PENDING/PROCESSING/FAILED are not searchable yet
        .where(or_(KnowledgeChunk.document_id.is_(None), KnowledgeDocument.status == "INDEXED"))
        .order_by(KnowledgeChunk.created_at, KnowledgeChunk.chunk_index)
    )
    rows = (await db.execute(stmt)).all()

    chunks: List[IndexedChunk] = []
    vectors: List[np.ndarray] = []
    vec_pos: List[int] = []
    for pos, r in enumerate(rows):
        is_current = r.embedding_model == current
        chunks.append(
            IndexedChunk(
                id=str(r.id), content=r.content, source_type=r.source_type or "DOCUMENT", filename=r.filename,
                section=r.section_title, page=r.page_number,
                document_id=str(r.document_id) if r.document_id else None,
                product_id=str(r.product_id) if r.product_id else None,
                has_vector=is_current,
            )
        )
        if need_vectors and is_current and r.embedding:
            vectors.append(from_bytes(r.embedding))
            vec_pos.append(pos)

    index = TenantIndex(
        token=token,
        chunks=chunks,
        bm25=BM25Index([lexical_tokens(c.content) for c in chunks]),
        id_pos={c.id: i for i, c in enumerate(chunks)},
        has_blobs=need_vectors,
    )
    if need_vectors and vectors:
        # keep only vectors sharing the majority dimension (guards against mixed/mocked models)
        dim = len(vectors[0])
        keep = [i for i, v in enumerate(vectors) if len(v) == dim]
        index.matrix = normalize(np.vstack([vectors[i] for i in keep]))
        index.row_pos = np.array([vec_pos[i] for i in keep], dtype=np.int64)
        index.row_source = np.array([chunks[vec_pos[i]].source_type for i in keep], dtype=object)

    _index_cache[key] = index
    _index_cache.move_to_end(key)
    while len(_index_cache) > _MAX_CACHED_TENANTS:
        _index_cache.popitem(last=False)
    return index


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class NumpyVectorStore:
    name = "numpy"
    needs_blobs = True

    async def search(
        self, db: AsyncSession, tenant_id: uuid.UUID, index: TenantIndex, qvec: np.ndarray,
        k: int, source_type: Optional[str],
    ) -> List[Tuple[int, float]]:
        if index.matrix is None or index.row_pos is None or index.matrix.shape[1] != len(qvec):
            return []
        q = normalize(np.asarray(qvec, dtype=np.float32)[None, :])[0]
        sims = index.matrix @ q
        if source_type and index.row_source is not None:
            mask = index.row_source == source_type
            if not mask.any():
                return []
            sims = np.where(mask, sims, -2.0)
        k = min(k, len(sims))
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.argsort(-sims[top])]
        return [(int(index.row_pos[i]), float(sims[i])) for i in top if sims[i] > -1.5]

    async def upsert(self, db: AsyncSession, rows: Sequence[Tuple[uuid.UUID, uuid.UUID, str, Sequence[float]]]) -> None:
        return None  # canonical blobs are the index


class PgVectorStore:
    name = "pgvector"
    needs_blobs = False

    async def search(
        self, db: AsyncSession, tenant_id: uuid.UUID, index: TenantIndex, qvec: np.ndarray,
        k: int, source_type: Optional[str],
    ) -> List[Tuple[int, float]]:
        from pgvector import Vector

        params = {"t": str(tenant_id), "q": Vector(list(map(float, qvec))).to_text(), "k": k, "s": source_type}
        sql = text(
            "SELECT CAST(chunk_id AS text) AS chunk_id, "
            "1 - (embedding <=> CAST(CAST(:q AS text) AS vector)) AS score "
            "FROM knowledge_vectors "
            "WHERE tenant_id = CAST(:t AS uuid) AND (CAST(:s AS text) IS NULL OR source_type = CAST(:s AS text)) "
            "ORDER BY embedding <=> CAST(CAST(:q AS text) AS vector) LIMIT :k"
        )
        try:
            async with db.begin_nested():
                await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        except Exception:  # pgvector < 0.8 has no iterative scan
            pass
        rows = (await db.execute(sql, params)).all()
        return [(index.id_pos[r.chunk_id], float(r.score)) for r in rows if r.chunk_id in index.id_pos]

    async def upsert(self, db: AsyncSession, rows: Sequence[Tuple[uuid.UUID, uuid.UUID, str, Sequence[float]]]) -> None:
        if not rows:
            return
        from pgvector import Vector

        sql = text(
            "INSERT INTO knowledge_vectors (chunk_id, tenant_id, source_type, embedding) "
            "VALUES (CAST(:c AS uuid), CAST(:t AS uuid), :s, CAST(CAST(:v AS text) AS vector)) "
            "ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding, source_type = EXCLUDED.source_type"
        )
        await db.execute(
            sql,
            [
                {"c": str(cid), "t": str(tid), "s": stype, "v": Vector(list(map(float, vec))).to_text()}
                for cid, tid, stype, vec in rows
            ],
        )


_numpy_store = NumpyVectorStore()
_pg_store = PgVectorStore()
_active: str = "numpy"


def get_vector_store() -> "NumpyVectorStore | PgVectorStore":
    return _pg_store if _active == "pgvector" else _numpy_store


def active_backend() -> str:
    return _active


async def init_vector_backend(engine: AsyncEngine) -> str:
    """Detect/prepare pgvector (idempotent). Falls back to numpy when the extension is not installed."""
    global _active
    _active = "numpy"
    if engine.dialect.name != "postgresql" or settings.VECTOR_BACKEND.lower() == "numpy":
        return _active
    dim = settings.EMBEDDING_DIM
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS knowledge_vectors ("
                    "chunk_id uuid PRIMARY KEY REFERENCES knowledge_chunks(id) ON DELETE CASCADE, "
                    "tenant_id uuid NOT NULL, source_type varchar(20) NOT NULL DEFAULT 'DOCUMENT', "
                    f"embedding vector({dim}) NOT NULL)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_hnsw "
                    "ON knowledge_vectors USING hnsw (embedding vector_cosine_ops)"
                )
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_tenant ON knowledge_vectors (tenant_id)")
            )
        _active = "pgvector"
        logger.info("Vector backend: pgvector (HNSW, dim=%s)", dim)
    except Exception as exc:
        logger.info("pgvector unavailable (%s); using the numpy vector backend.", str(exc).splitlines()[0][:120])
    return _active


async def sync_pgvector(db: AsyncSession, tenant_id: Optional[uuid.UUID] = None) -> int:
    """Backfill the pgvector sidecar from canonical embeddings. Returns rows written."""
    if _active != "pgvector":
        return 0
    sql = (
        "SELECT c.id, c.tenant_id, c.source_type, c.embedding FROM knowledge_chunks c "
        "LEFT JOIN knowledge_vectors v ON v.chunk_id = c.id "
        "WHERE v.chunk_id IS NULL AND c.embedding IS NOT NULL AND c.embedding_model = :m"
    )
    params: Dict[str, object] = {"m": embedding_model_name()}
    if tenant_id:
        sql += " AND c.tenant_id = CAST(:t AS uuid)"
        params["t"] = str(tenant_id)
    rows = (await db.execute(text(sql), params)).all()
    batch = [
        (r.id, r.tenant_id, r.source_type, from_bytes(r.embedding))
        for r in rows
        if len(r.embedding) == 4 * settings.EMBEDDING_DIM
    ]
    for i in range(0, len(batch), 500):
        await _pg_store.upsert(db, batch[i:i + 500])
    return len(batch)
