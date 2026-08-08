from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.common.knowledge_scope import KnowledgeScope
from app.repositories.knowledge import KnowledgeRepository


@dataclass(frozen=True)
class KnowledgeCitation:
    chunk_id: str
    document_id: str
    version_id: str
    content: str
    score: float
    title_path: str
    page_number: int | None
    metadata: dict[str, Any]


class WealthKnowledgeRetriever:
    """Application adapter over PostgreSQL/pgvector retrieval."""

    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    async def search(
        self,
        *,
        query: str,
        query_embedding: list[float] | None = None,
        top_k: int = 5,
        knowledge_base_id: str | None = None,
        category: str | None = None,
        scope: KnowledgeScope | None = None,
    ) -> list[KnowledgeCitation]:
        if query_embedding is not None:
            pairs = await self.repository.search_hybrid(
                query=query,
                query_embedding=query_embedding,
                top_k=top_k,
                knowledge_base_id=knowledge_base_id,
                category=category,
                scope=scope,
            )
        else:
            chunks = await self.repository.search_text(
                query=query,
                top_k=top_k,
                knowledge_base_id=knowledge_base_id,
                category=category,
                scope=scope,
            )
            pairs = [(chunk, 1.0) for chunk in chunks]
        return [
            KnowledgeCitation(
                chunk_id=str(chunk.id),
                document_id=str(chunk.document_id),
                version_id=str(chunk.version_id),
                content=chunk.content,
                score=float(score),
                title_path=chunk.title_path,
                page_number=chunk.page_number,
                metadata=chunk.metadata_json or {},
            )
            for chunk, score in pairs
        ]
