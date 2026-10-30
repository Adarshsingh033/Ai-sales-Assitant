"""
Knowledge Management endpoints for Tenant Admin.
Handles document uploads (PDF, DOCX, TXT, MD, CSV), background indexing, re-indexing and semantic search testing.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.knowledge import KnowledgeDocument
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgeReindexResponse,
    KnowledgeSearchMatch,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services.jobs import JobQueue
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenant-admin/knowledge", tags=["Tenant Admin Knowledge Base"])

_ALLOWED = ("pdf", "docx", "txt", "md", "csv")
_MAX_BYTES = 15 * 1024 * 1024


def _tenant(user: User) -> uuid.UUID:
    if not user.tenant_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tenant ID is required.")
    return user.tenant_id


@router.post(
    "/upload",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload & Index Document",
)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> KnowledgeDocumentResponse:
    tenant_id = _tenant(current_user)
    filename = file.filename or "uploaded_document"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext}'. Supported formats: PDF, DOCX, TXT, MD, CSV.",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    if len(file_bytes) > _MAX_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File size exceeds maximum allowed limit (15MB).")

    try:
        doc = await RAGService.create_document(db, tenant_id, filename, file_bytes, ext)
    except ValueError as exc:  # unreadable / unsupported content
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Document extraction failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not read the document: {exc}"
        ) from exc

    if doc.status == "PENDING":
        if len(doc.content_text or "") <= settings.KNOWLEDGE_INLINE_MAX_CHARS:
            doc = await RAGService.index_document_record(db, doc.id)  # small: finish before responding
        else:
            await JobQueue.enqueue(db, "INDEX_DOCUMENT", {"document_id": str(doc.id)}, tenant_id=tenant_id)
    return KnowledgeDocumentResponse.model_validate(doc)


@router.get("/documents", response_model=KnowledgeDocumentListResponse, summary="List Knowledge Documents")
async def list_documents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> KnowledgeDocumentListResponse:
    tenant_id = _tenant(current_user)
    rows = (
        await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.tenant_id == tenant_id)
            .order_by(KnowledgeDocument.created_at.desc())
        )
    ).scalars().all()
    return KnowledgeDocumentListResponse(
        items=[KnowledgeDocumentResponse.model_validate(d) for d in rows], total=len(rows)
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete Knowledge Document")
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> None:
    doc = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == current_user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    await RAGService.delete_document(db, doc)


@router.post(
    "/documents/{document_id}/reindex",
    response_model=KnowledgeDocumentResponse,
    summary="Re-index One Document (retry a failed one or apply new chunking)",
)
async def reindex_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> KnowledgeDocumentResponse:
    doc = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == current_user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if not doc.content_text:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document predates stored text; please upload it again to re-index.",
        )
    return KnowledgeDocumentResponse.model_validate(await RAGService.reindex_document(db, doc.id))


@router.post(
    "/reindex",
    response_model=KnowledgeReindexResponse,
    summary="Re-embed All Stale Knowledge (after an embedding-model change)",
)
async def reindex_all(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> KnowledgeReindexResponse:
    tenant_id = _tenant(current_user)
    stale = await RAGService.count_stale(db, tenant_id)
    if stale:
        await JobQueue.enqueue(db, "REINDEX_TENANT", {}, tenant_id=tenant_id, dedupe=True)
    return KnowledgeReindexResponse(queued=bool(stale), stale_chunks=stale)


@router.post("/test-search", response_model=KnowledgeSearchResponse, summary="Test Knowledge Semantic Search")
async def test_search(
    body: KnowledgeSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> KnowledgeSearchResponse:
    tenant_id = _tenant(current_user)
    res = await RAGService.retrieve(db, tenant_id, body.query, top_k=body.top_k, source_type="DOCUMENT")
    return KnowledgeSearchResponse(
        query=body.query,
        matches=[KnowledgeSearchMatch(**{k: m.get(k) for k in KnowledgeSearchMatch.model_fields}) for m in res.matches],
        confidence=res.confidence,
        backend=res.backend,
    )
