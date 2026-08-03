"""Compare pgvector and Milvus retrieval for the same embeddings.

The script is read-only against PostgreSQL and Milvus. It does not change the
application backend selection.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.infrastructure.vector_store.factory import create_vector_store


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    try:
        vector = (await provider.embed([args.query]))[0]
        pg = create_vector_store(settings.model_copy(update={"vector_store_backend": "pgvector"}), database)
        milvus = create_vector_store(settings.model_copy(update={"vector_store_backend": "milvus"}), database)
        pg_hits = await pg.search(vector, top_k=args.top_k)
        milvus_hits = await milvus.search(vector, top_k=args.top_k)
        pg_ids = [hit.id for hit in pg_hits]
        milvus_ids = [hit.id for hit in milvus_hits]
        overlap = len(set(pg_ids) & set(milvus_ids))
        print(json.dumps({
            "query": args.query,
            "top_k": args.top_k,
            "pgvector": [hit.__dict__ for hit in pg_hits],
            "milvus": [hit.__dict__ for hit in milvus_hits],
            "overlap": overlap,
        }, ensure_ascii=False, indent=2, default=str))
    finally:
        await provider.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
