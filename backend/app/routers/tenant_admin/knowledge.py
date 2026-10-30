"""
Knowledge Management endpoints for Tenant Admin.
Handles document uploads (PDF, DOCX, TXT), indexing, and semantic search testing.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import require_tenant_admin
from app.models.knowledge import KnowledgeDocument
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgeSearchMatch,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services.rag_service import RAGService

router = APIRouter(prefix="/tenant-admin/knowledge", tags=["Tenant Admin Knowledge Base"])


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
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID is required to manage knowledge documents.",
        )

    filename = file.filename or "uploaded_document"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ("pdf", "docx", "txt", "md"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext}'. Supported formats: PDF, DOCX, TXT, MD.",
        )

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Max 15MB file size limit for MVP
    if len(file_bytes) > 15 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds maximum allowed limit (15MB).",
        )

    try:
        doc = await RAGService.index_document(
            db=db,
            tenant_id=current_user.tenant_id,
            filename=filename,
            file_bytes=file_bytes,
            file_type=ext,
        )
        return KnowledgeDocumentResponse.model_validate(doc)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process and index document: {str(exc)}",
        ) from exc


@router.get(
    "/documents",
    response_model=KnowledgeDocumentListResponse,
    summary="List Knowledge Documents",
)
async def list_documents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> KnowledgeDocumentListResponse:
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID is required.",
        )

    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == current_user.tenant_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    return KnowledgeDocumentListResponse(
        items=[KnowledgeDocumentResponse.model_validate(d) for d in rows],
        total=len(rows),
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Knowledge Document",
)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> None:
    stmt = select(KnowledgeDocument).where(
        KnowledgeDocument.id == document_id,
        KnowledgeDocument.tenant_id == current_user.tenant_id,
    )
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    await db.delete(doc)
    await db.commit()


@router.post(
    "/test-search",
    response_model=KnowledgeSearchResponse,
    summary="Test Knowledge Semantic Search",
)
async def test_search(
    body: KnowledgeSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_tenant_admin),
) -> KnowledgeSearchResponse:
    if not current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID is required.",
        )

    matches = await RAGService.search_knowledge(
        db=db,
        tenant_id=current_user.tenant_id,
        query=body.query,
        top_k=body.top_k,
    )

    return KnowledgeSearchResponse(
        query=body.query,
        matches=[KnowledgeSearchMatch(**m) for m in matches],
    )
