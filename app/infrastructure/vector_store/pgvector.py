from __future__ import annotations

from sqlalchemy import delete, select

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion
from app.ports.vector_store import VectorDocument, VectorHit


class PgVectorStore:
    """VectorStore adapter backed by PostgreSQL + pgvector.

    PostgreSQL remains the reference implementation. The adapter deliberately
    uses the same port as Milvus so retrieval can be switched by configuration.
    """

    def __init__(self, database):
        self.database = database

    async def upsert(self, documents: list[VectorDocument]) -> None:
        if not documents:
            return
        async with self.database.session_factory() as session:
            for document in documents:
                row = await session.get(KnowledgeChunk, document.id)
                if row is None:
                    row = KnowledgeChunk(
                        id=document.id,
                        document_id=document.metadata.get("document_id", document.id),
                        version_id=document.metadata.get("version_id", document.id),
                        chunk_index=int(document.metadata.get("chunk_index", 0)),
                        content=document.text,
                        content_hash=document.metadata.get("content_hash", ""),
                        title_path=document.metadata.get("title_path", ""),
                        page_number=document.metadata.get("page_number"),
                        metadata_json=document.metadata,
                        embedding=document.vector,
                        status=document.metadata.get("status", "active"),
                    )
                    session.add(row)
                else:
                    row.content = document.text
                    row.metadata_json = document.metadata
                    row.embedding = document.vector
                    row.status = document.metadata.get("status", row.status)
            await session.commit()

    async def search(self, vector: list[float], top_k: int = 5, filters: dict | None = None) -> list[VectorHit]:
        async with self.database.session_factory() as session:
            statement = (
                select(KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                .where(
                    KnowledgeChunk.status == "active",
                    KnowledgeChunk.deleted_at.is_(None),
                    KnowledgeDocument.status == "active",
                    KnowledgeDocument.deleted_at.is_(None),
                    KnowledgeDocumentVersion.status == "active",
                    KnowledgeChunk.embedding.is_not(None),
                )
            )
            filters = filters or {}
            if filters.get("knowledge_base_id") is not None:
                statement = statement.where(KnowledgeDocument.knowledge_base_id == filters["knowledge_base_id"])
            if filters.get("category") is not None:
                statement = statement.where(KnowledgeDocument.category == filters["category"])
            score = (1.0 - KnowledgeChunk.embedding.cosine_distance(vector)).label("score")
            result = await session.execute(statement.add_columns(score).order_by(score.desc()).limit(top_k))
            return [
                VectorHit(
                    id=str(chunk.id),
                    score=float(score_value or 0.0),
                    text=chunk.content,
                    metadata={
                        **(chunk.metadata_json or {}),
                        "document_id": str(chunk.document_id),
                        "version_id": str(chunk.version_id),
                        "title_path": chunk.title_path,
                        "chunk_index": chunk.chunk_index,
                    },
                )
                for chunk, _document, _version, score_value in result.all()
            ]

    async def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        async with self.database.session_factory() as session:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))
            await session.commit()
