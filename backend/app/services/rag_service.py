"""
RAG Knowledge Service.
Extracts text from PDF/DOCX/TXT files, chunks text, generates embeddings via FastEmbed,
and performs tenant-scoped cosine similarity vector search.
"""
import io
import json
import logging
import math
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument

logger = logging.getLogger(__name__)

# Cached singleton model instance
_EMBEDDING_MODEL = None


def get_embedding_model():
    """Lazily load and cache FastEmbed model instance."""
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        from fastembed import TextEmbedding
        logger.info("Initializing FastEmbed model (BAAI/bge-small-en-v1.5)...")
        _EMBEDDING_MODEL = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _EMBEDDING_MODEL


class RAGService:
    @staticmethod
    def extract_text(file_bytes: bytes, file_type: str) -> str:
        """
        Extract plain text from uploaded file bytes based on file extension.
        """
        ext = file_type.lower().strip(".").strip()
        if ext == "pdf":
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages_text).strip()

        elif ext in ("docx", "doc"):
            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs).strip()

        elif ext in ("txt", "md", "csv"):
            return file_bytes.decode("utf-8", errors="replace").strip()

        else:
            raise ValueError(f"Unsupported file type: {file_type}. Supported: pdf, docx, txt")

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
        """
        Split text into overlapping chunks cleanly.
        """
        text = text.strip()
        if not text:
            return []

        chunks = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + chunk_size, text_len)
            # If not at the end of text, find nearest space/newline to avoid cutting words
            if end < text_len:
                boundary = text.rfind(" ", start + overlap, end)
                if boundary != -1:
                    end = boundary

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            if end >= text_len:
                break
            start = end - overlap if (end - overlap) > start else end

        return chunks

    @staticmethod
    def generate_embeddings(texts: List[str]) -> List[List[float]]:
        """
        Generate dense vector embeddings using local FastEmbed model.
        """
        if not texts:
            return []
        model = get_embedding_model()
        embeddings_generator = model.embed(texts)
        return [[float(x) for x in emb] for emb in embeddings_generator]

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """
        Compute cosine similarity between two float vectors.
        """
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return dot_product / (norm1 * norm2)

    @classmethod
    async def index_document(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        filename: str,
        file_bytes: bytes,
        file_type: str,
    ) -> KnowledgeDocument:
        """
        Extract text, generate chunks & embeddings, and store them in the database.
        """
        text = cls.extract_text(file_bytes, file_type)
        chunks = cls.chunk_text(text)

        doc = KnowledgeDocument(
            tenant_id=tenant_id,
            filename=filename,
            file_type=file_type.lower().strip("."),
            file_size=len(file_bytes),
            status="INDEXED",
            chunk_count=len(chunks),
        )
        db.add(doc)
        await db.flush()

        if chunks:
            embeddings = cls.generate_embeddings(chunks)
            chunk_records = []
            for idx, (chunk_text_str, emb) in enumerate(zip(chunks, embeddings)):
                chunk_records.append(
                    KnowledgeChunk(
                        tenant_id=tenant_id,
                        document_id=doc.id,
                        chunk_index=idx,
                        content=chunk_text_str,
                        embedding_json=json.dumps(emb),
                        token_count=len(chunk_text_str.split()),
                    )
                )
            db.add_all(chunk_records)

        await db.commit()
        await db.refresh(doc)
        return doc

    @classmethod
    async def search_knowledge(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        query: str,
        top_k: int = 4,
        similarity_threshold: float = 0.35,
    ) -> List[Dict[str, Any]]:
        """
        Search tenant knowledge chunks using vector cosine similarity.
        """
        query_text = query.strip()
        if not query_text:
            return []

        query_embedding = cls.generate_embeddings([query_text])[0]

        # Fetch tenant chunks
        stmt = (
            select(KnowledgeChunk, KnowledgeDocument.filename)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeChunk.tenant_id == tenant_id)
        )
        result = await db.execute(stmt)
        rows = result.all()

        scored_chunks = []
        for chunk, filename in rows:
            emb = json.loads(chunk.embedding_json)
            score = cls.cosine_similarity(query_embedding, emb)
            if score >= similarity_threshold:
                scored_chunks.append({
                    "chunk_id": str(chunk.id),
                    "filename": filename,
                    "content": chunk.content,
                    "score": round(score, 4),
                })

        scored_chunks.sort(key=lambda x: x["score"], reverse=True)
        return scored_chunks[:top_k]
