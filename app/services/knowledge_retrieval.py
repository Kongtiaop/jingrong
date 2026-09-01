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
    # 向量余弦相似度（1 - cosine_distance）；无向量/纯文本降级时为 None。
    # RRF score 是排名融合值（相关与无关都约 0.016），不能直接当"证据置信度"；
    # 拒答/转人工应按真实语义相似度判定。
    vector_similarity: float | None = None


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
        rrf_k: int | None = None,
    ) -> list[KnowledgeCitation]:
        if query_embedding is not None:
            pairs = await self.repository.search_hybrid(
                query=query,
                query_embedding=query_embedding,
                top_k=top_k,
                knowledge_base_id=knowledge_base_id,
                category=category,
                scope=scope,
                rrf_k=rrf_k,
            )
        else:
            chunks = await self.repository.search_text(
                query=query,
                top_k=top_k,
                knowledge_base_id=knowledge_base_id,
                category=category,
                scope=scope,
            )
            pairs = [(chunk, 1.0, None) for chunk in chunks]
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
                vector_similarity=sim,
            )
            for chunk, score, sim in pairs
        ]
