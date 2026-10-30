"""
Pydantic schemas for Knowledge Document and RAG retrieval.
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocumentResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    filename: str
    file_type: str
    file_size: int
    status: str  # PENDING | PROCESSING | INDEXED | FAILED
    chunk_count: int
    version: int = 1
    error_message: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeDocumentListResponse(BaseModel):
    items: List[KnowledgeDocumentResponse]
    total: int


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=500)
    top_k: int = Field(default=4, ge=1, le=10)


class KnowledgeSearchMatch(BaseModel):
    chunk_id: str
    filename: str
    content: str
    score: float
    fused_score: Optional[float] = None
    section: Optional[str] = None
    page: Optional[int] = None
    source_type: Optional[str] = None


class KnowledgeSearchResponse(BaseModel):
    query: str
    matches: List[KnowledgeSearchMatch]
    confidence: float = 0.0
    backend: Optional[str] = None


class KnowledgeReindexResponse(BaseModel):
    queued: bool
    stale_chunks: int
    detail: Dict[str, Any] = {}
