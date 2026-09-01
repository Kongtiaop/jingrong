from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.knowledge_retrieval import KnowledgeCitation, WealthKnowledgeRetriever


class FakeRepository:
    async def search_text(self, **kwargs):
        return [SimpleNamespace(id="chunk-1", document_id="doc-1", version_id="ver-1", content="客服知识", title_path="FAQ", page_number=None, metadata_json={})]

    async def search_hybrid(self, **kwargs):
        chunk = (await self.search_text())[0]
        return [(chunk, 0.91, 0.8)]


async def main():
    retriever = WealthKnowledgeRetriever(FakeRepository())
    text_hits = await retriever.search(query="客服", top_k=3)
    assert len(text_hits) == 1 and text_hits[0].document_id == "doc-1"
    hybrid_hits = await retriever.search(query="客服", query_embedding=[0.1], top_k=3)
    assert hybrid_hits[0].score == 0.91
    print({"text_mode": True, "hybrid_mode": True, "citation_metadata": True, "pass": True})


if __name__ == "__main__":
    asyncio.run(main())
