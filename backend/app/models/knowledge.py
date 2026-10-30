"""
Knowledge Management ORM Models.
Stores uploaded documents, product records and their chunked text with vector embeddings.

Embeddings are stored canonically as little-endian float32 bytes (`embedding`) so the data is portable
across SQLite and PostgreSQL. When the pgvector extension is installed an accelerated sidecar table
(`knowledge_vectors`, HNSW index) is maintained by `app.services.ai.vector_store`.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)  # pdf, docx, txt, md, csv
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="INDEXED"
    )  # PENDING, PROCESSING, INDEXED, FAILED
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Extracted plain text is kept so documents can be re-indexed (new model / chunking) without the file.
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeDocument id={self.id} filename={self.filename} chunks={self.chunk_count}>"


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A chunk belongs either to an uploaded document or to a catalog product.
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    product_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="DOCUMENT")  # DOCUMENT | PRODUCT

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    section_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # float32 little-endian bytes; `embedding_model` records which model produced it (stale rows are re-embedded)
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    document: Mapped[Optional["KnowledgeDocument"]] = relationship(
        "KnowledgeDocument",
        back_populates="chunks",
    )

    __table_args__ = (
        Index("ix_knowledge_chunks_tenant_doc", "tenant_id", "document_id"),
        Index("ix_knowledge_chunks_tenant_source", "tenant_id", "source_type"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeChunk id={self.id} doc={self.document_id} idx={self.chunk_index}>"
