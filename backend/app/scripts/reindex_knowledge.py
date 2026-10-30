"""
Rebuild the AI search index.

    python -m app.scripts.reindex_knowledge            # re-embed stale chunks, index all products, sync pgvector
    python -m app.scripts.reindex_knowledge --products-only

Run it once after `alembic upgrade head` (existing knowledge is tagged as produced by the old embedding model) and
whenever EMBEDDING_MODEL changes. Safe to run repeatedly; it only touches what is out of date.
"""
import argparse
import asyncio
import logging

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.models.product import Product
from app.services.ai.embeddings import embedding_model_name
from app.services.ai.vector_store import init_vector_backend, sync_pgvector
from app.services.rag_service import RAGService

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("reindex")


async def main(products_only: bool) -> None:
    backend = await init_vector_backend(engine)
    logger.info("Embedding model: %s | vector backend: %s", embedding_model_name(), backend)
    async with AsyncSessionLocal() as db:
        if not products_only:
            stale = await RAGService.count_stale(db)
            logger.info("%d chunk(s) need re-embedding", stale)
            logger.info("Re-embedded %d chunk(s)", await RAGService.reindex_stale(db))

        products = (await db.execute(select(Product))).scalars().all()
        for p in products:
            await RAGService.index_product(db, p)
        logger.info("Indexed %d product(s)", len(products))

        logger.info("pgvector rows synced: %d", await sync_pgvector(db))
        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--products-only", action="store_true")
    asyncio.run(main(parser.parse_args().products_only))
