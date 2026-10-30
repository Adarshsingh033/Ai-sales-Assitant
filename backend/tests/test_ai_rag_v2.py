"""RAG v2: structure-aware chunking, ingestion lifecycle, hybrid retrieval, isolation, products, re-embedding."""
import io
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services.ai import chunking
from app.services.ai.embeddings import HashEmbeddingProvider, embedding_model_name, to_bytes
from app.services.ai.lexical import BM25Index, lexical_tokens
from app.services.rag_service import RAGService
from tests.factories import make_product, make_tenant


# ----------------------------------------------------------------------------- chunking
def test_chunks_never_mix_sections_and_keep_pages():
    blocks = [
        chunking.TextBlock("Pricing", is_heading=True),
        chunking.TextBlock("Starter costs $50 per month. " * 30, page=1),
        chunking.TextBlock("Support", is_heading=True),
        chunking.TextBlock("Premium support answers within one hour.", page=2),
    ]
    chunks = chunking.chunk_blocks(blocks, target_chars=300)
    assert {c.section for c in chunks} == {"Pricing", "Support"}
    assert all("support" not in c.content.lower() for c in chunks if c.section == "Pricing")
    support = [c for c in chunks if c.section == "Support"]
    assert support[0].page == 2
    assert len([c for c in chunks if c.section == "Pricing"]) >= 2  # long section split on sentence boundaries


def test_chunk_overlap_carries_context_across_boundaries():
    sentences = [f"Sentence number {i} talks about feature {i}." for i in range(30)]
    chunks = chunking.chunk_blocks([chunking.TextBlock(" ".join(sentences))], target_chars=200, overlap_chars=60)
    assert len(chunks) > 3
    first_tail = chunks[0].content.split(". ")[-1]
    assert first_tail.split()[0] in chunks[1].content  # last sentence repeated at the start of the next chunk


def test_extract_markdown_csv_and_docx_structure():
    md = b"# Refund Policy\n\nRefunds are possible within 30 days.\n\n## Shipping\nWe ship worldwide."
    blocks = chunking.extract_blocks(md, "md")
    assert [(b.text, b.is_heading) for b in blocks][:2] == [("Refund Policy", True), ("Refunds are possible within 30 days.", False)]

    csv_blocks = chunking.extract_blocks(b"plan,price\nStarter,50\nPro,200\n", "csv", "plans.csv")
    assert csv_blocks[0].text == "plan: Starter; price: 50"

    import docx

    document = docx.Document()
    document.add_heading("Plans", level=1)
    document.add_paragraph("Choose the plan that fits.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Plan", "Seats"
    table.rows[1].cells[0].text, table.rows[1].cells[1].text = "Team", "25"
    buf = io.BytesIO()
    document.save(buf)
    docx_blocks = chunking.extract_blocks(buf.getvalue(), "docx")
    assert docx_blocks[0].is_heading and docx_blocks[0].text == "Plans"
    assert any(b.text == "Plan: Team; Seats: 25" for b in docx_blocks)


def test_hindi_text_stays_tokenisable_and_bm25_matches_exact_terms():
    assert "कीमत" in lexical_tokens("इसकी कीमत क्या है?")
    docs = [lexical_tokens(t) for t in ["model ZX-9000 warranty is two years", "general pricing overview for all plans"]]
    assert BM25Index(docs).search(lexical_tokens("ZX-9000 warranty"))[0][0] == 0


def test_hash_embeddings_are_normalised_and_similar_texts_score_higher():
    p = HashEmbeddingProvider(384)
    a, b, c = p.embed(["enterprise plan pricing", "pricing of the enterprise plan", "bananas and apples"])
    cos = RAGService.cosine_similarity
    assert cos(a, b) > cos(a, c) + 0.2
    assert abs(sum(x * x for x in a) - 1.0) < 1e-5


# ----------------------------------------------------------------------------- ingestion lifecycle
FAQ = (
    b"# Warranty\nThe ZX-9000 router has a two year warranty covering hardware faults.\n\n"
    b"# Pricing\nThe Enterprise plan costs 500 dollars per month and includes 50 seats.\n\n"
    b"# Support\nPremium support responds within one hour on business days."
)


@pytest.mark.asyncio
async def test_document_is_not_searchable_until_indexed_and_search_is_hybrid(test_db):
    tenant = await make_tenant(test_db)
    doc = await RAGService.create_document(test_db, tenant.id, "faq.md", FAQ, "md")
    assert doc.status == "PENDING" and doc.chunk_count >= 3
    assert await RAGService.search_knowledge(test_db, tenant.id, "ZX-9000 warranty") == []

    doc = await RAGService.index_document_record(test_db, doc.id)
    assert doc.status == "INDEXED" and doc.embedding_model == embedding_model_name()

    res = await RAGService.retrieve(test_db, tenant.id, "ZX-9000 warranty", top_k=3, source_type="DOCUMENT")
    assert res.matches and "two year warranty" in res.matches[0]["content"]
    assert res.matches[0]["section"] == "Warranty"
    assert 0 < res.confidence <= 1
    # `score` is the dense cosine similarity (not the fused rank score)
    assert res.matches[0]["score"] == res.confidence


@pytest.mark.asyncio
async def test_multilingual_query_retrieves_hindi_document(test_db):
    tenant = await make_tenant(test_db)
    hindi = "एंटरप्राइज़ प्लान की कीमत प्रति माह पाँच सौ डॉलर है।\n\nप्रीमियम सपोर्ट एक घंटे में जवाब देता है।".encode()
    await RAGService.index_document(test_db, tenant.id, "hindi_faq.txt", hindi, "txt")
    matches = await RAGService.search_knowledge(test_db, tenant.id, "एंटरप्राइज़ प्लान की कीमत क्या है")
    assert matches and "कीमत" in matches[0]["content"]


@pytest.mark.asyncio
async def test_tenant_isolation(test_db):
    a, b = await make_tenant(test_db, "Tenant A"), await make_tenant(test_db, "Tenant B")
    await RAGService.index_document(test_db, a.id, "secret.txt", b"Project Falcon launch budget is confidential.", "txt")
    assert await RAGService.search_knowledge(test_db, a.id, "Project Falcon budget")
    assert await RAGService.search_knowledge(test_db, b.id, "Project Falcon budget") == []


@pytest.mark.asyncio
async def test_identical_content_is_deduplicated_and_reupload_replaces_old_version(test_db):
    tenant = await make_tenant(test_db)
    first = await RAGService.index_document(test_db, tenant.id, "policy.txt", b"Returns accepted within 14 days.", "txt")
    again = await RAGService.index_document(test_db, tenant.id, "copy.txt", b"Returns accepted within 14 days.", "txt")
    assert again.id == first.id  # same content hash → no duplicate chunks

    v2 = await RAGService.index_document(test_db, tenant.id, "policy.txt", b"Returns accepted within 30 days.", "txt")
    assert v2.id != first.id and v2.version == 2
    docs = (await test_db.execute(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant.id))).scalars().all()
    assert [d.id for d in docs] == [v2.id]
    contents = [m["content"] for m in await RAGService.search_knowledge(test_db, tenant.id, "returns accepted within days")]
    assert contents and all("30 days" in c for c in contents)


@pytest.mark.asyncio
async def test_failed_indexing_is_recorded_and_can_be_retried(test_db):
    tenant = await make_tenant(test_db)
    doc = await RAGService.create_document(test_db, tenant.id, "faq.md", FAQ, "md")
    with patch.object(RAGService, "generate_embeddings", side_effect=RuntimeError("model download failed")):
        failed = await RAGService.index_document_record(test_db, doc.id)
    assert failed.status == "FAILED" and "model download failed" in failed.error_message

    fixed = await RAGService.reindex_document(test_db, doc.id)
    assert fixed.status == "INDEXED" and fixed.error_message is None
    assert await RAGService.search_knowledge(test_db, tenant.id, "premium support responds within one hour")


# ----------------------------------------------------------------------------- products + stale embeddings
@pytest.mark.asyncio
async def test_products_are_indexed_updated_and_removed(test_db):
    tenant = await make_tenant(test_db)
    p = await make_product(test_db, tenant, name="OmniSuite ERP", sku="OMNI-1", price=12000, features="Ledger, billing")
    await RAGService.index_product(test_db, p)

    found = await RAGService.retrieve(test_db, tenant.id, "OMNI-1 ledger", top_k=3, source_type="PRODUCT")
    assert found.matches and found.matches[0]["product_id"] == str(p.id)
    assert "12000.00" in found.matches[0]["content"]
    assert await RAGService.search_knowledge(test_db, tenant.id, "OmniSuite ERP") == []  # documents only by default

    p.price = 15000
    await RAGService.index_product(test_db, p)
    assert "15000.00" in (await RAGService.retrieve(test_db, tenant.id, "OMNI-1", source_type="PRODUCT")).matches[0]["content"]

    p.is_active = False
    await RAGService.index_product(test_db, p)
    assert (await RAGService.retrieve(test_db, tenant.id, "OMNI-1", source_type="PRODUCT")).matches == []


@pytest.mark.asyncio
async def test_stale_embeddings_never_feed_dense_search_until_reindexed(test_db):
    tenant = await make_tenant(test_db)
    await RAGService.index_document(test_db, tenant.id, "faq.md", FAQ, "md")
    assert await RAGService.count_stale(test_db, tenant.id) == 0

    chunks = (await test_db.execute(select(KnowledgeChunk).where(KnowledgeChunk.tenant_id == tenant.id))).scalars().all()
    for c in chunks:  # simulate a model switch: vectors from another model must never be compared with new queries
        c.embedding_model, c.embedding = "legacy:old-model", to_bytes([0.5] * 7)
    await test_db.commit()
    from app.services.ai.vector_store import invalidate_tenant_index
    invalidate_tenant_index(tenant.id)

    assert await RAGService.count_stale(test_db, tenant.id) == len(chunks)
    # degraded mode: text is still findable lexically, but outdated vectors contribute nothing (confidence 0)
    degraded = await RAGService.retrieve(test_db, tenant.id, "ZX-9000 warranty", source_type="DOCUMENT")
    assert degraded.matches and degraded.confidence == 0.0

    assert await RAGService.reindex_stale(test_db, tenant.id) == len(chunks)
    assert await RAGService.count_stale(test_db, tenant.id) == 0
    healthy = await RAGService.retrieve(test_db, tenant.id, "ZX-9000 warranty", source_type="DOCUMENT")
    assert healthy.matches and healthy.confidence > 0.3


@pytest.mark.asyncio
async def test_empty_or_unmatched_queries_return_nothing(test_db):
    tenant = await make_tenant(test_db)
    assert (await RAGService.retrieve(test_db, tenant.id, "   ")).matches == []
    await RAGService.index_document(test_db, tenant.id, "faq.md", FAQ, "md")
    res = await RAGService.retrieve(test_db, tenant.id, "quantum chromodynamics lagrangian", source_type="DOCUMENT")
    assert res.matches == [] and res.confidence == 0.0
