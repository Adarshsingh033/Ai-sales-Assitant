"""
Unit and Integration Tests for Groq LLM Client and FastEmbed RAG Service.
"""
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.services.groq_client import GroqClient, GroqClientError
from app.services.rag_service import RAGService


def test_chunk_text():
    text = "This is a sales test document. It contains information about enterprise pricing and discounts. Support is included."
    chunks = RAGService.chunk_text(text, chunk_size=50, overlap=10)
    assert len(chunks) >= 2
    assert "pricing" in "".join(chunks).lower()


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]

    assert pytest.approx(RAGService.cosine_similarity(v1, v2), 0.001) == 1.0
    assert pytest.approx(RAGService.cosine_similarity(v1, v3), 0.001) == 0.0
    assert RAGService.cosine_similarity([], []) == 0.0


def test_extract_text_txt():
    sample_bytes = b"Hello, welcome to our company sales knowledge base!"
    extracted = RAGService.extract_text(sample_bytes, "txt")
    assert "sales knowledge base" in extracted


def test_extract_text_unsupported():
    with pytest.raises(ValueError, match="Unsupported file type"):
        RAGService.extract_text(b"data", "exe")


@pytest.mark.asyncio
async def test_groq_client_missing_key():
    client = GroqClient(api_key="")
    with pytest.raises(GroqClientError, match="Groq API key is not configured"):
        await client.chat_completion([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_groq_client_mock_success():
    from unittest.mock import MagicMock
    client = GroqClient(api_key="mock_key")
    mock_resp_json = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hello! I am your AI sales assistant."
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_resp_json
        mock_post.return_value = mock_response

        reply = await client.chat_completion([{"role": "user", "content": "Hi"}])
        assert reply == "Hello! I am your AI sales assistant."


@pytest.mark.asyncio
async def test_rag_index_and_search(test_db: AsyncSession):
    tenant = Tenant(name="RAG Test Tenant", slug="rag-tenant")
    test_db.add(tenant)
    await test_db.flush()

    sales_faq_content = (
        "Enterprise Plan costs $500 per month and includes up to 50 sales users.\n"
        "Starter Plan costs $50 per month for small teams of up to 5 users.\n"
        "We offer a 14-day free trial on all plans with no credit card required."
    ).encode("utf-8")

    # Mock embeddings to return deterministic vectors
    def mock_embeddings(texts):
        results = []
        for t in texts:
            if "enterprise" in t.lower() or "price" in t.lower():
                results.append([0.9, 0.1, 0.0])
            else:
                results.append([0.1, 0.9, 0.0])
        return results

    with patch.object(RAGService, "generate_embeddings", side_effect=mock_embeddings):
        # Index document
        doc = await RAGService.index_document(
            db=test_db,
            tenant_id=tenant.id,
            filename="pricing_faq.txt",
            file_bytes=sales_faq_content,
            file_type="txt",
        )
        assert doc.chunk_count >= 1

        # Search for pricing
        results = await RAGService.search_knowledge(
            db=test_db,
            tenant_id=tenant.id,
            query="What is the price of the enterprise plan?",
            top_k=2,
            similarity_threshold=0.3,
        )
        assert len(results) >= 1
        assert "Enterprise Plan" in results[0]["content"]
        assert results[0]["score"] > 0.8
