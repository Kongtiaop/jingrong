from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Float, func, literal, or_, select, update

from app.db.session import Database
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion
from app.models.outbox import OutboxEvent
from app.common.knowledge_scope import KnowledgeScope


class KnowledgeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def ensure_default_base(self) -> KnowledgeBase:
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.name == "default"))
            base = result.scalar_one_or_none()
            if base is None:
                base = KnowledgeBase(id=str(uuid4()), name="default", description="默认知识库", status="active")
                session.add(base)
                await session.commit()
                await session.refresh(base)
            return base

    async def get_base(self, base_id: str) -> KnowledgeBase | None:
        try:
            UUID(base_id)
        except ValueError:
            return None
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == base_id, KnowledgeBase.status == "active"))
            return result.scalar_one_or_none()

    async def create_document(self, *, knowledge_base_id: str, document_key: str, file_name: str, source_path: str, file_type: str, file_size: int, content_hash: str, category: str = "general", permission_level: str = "public") -> tuple[KnowledgeDocument, str]:
        async with self.database.session_factory() as session:
            document = KnowledgeDocument(id=str(uuid4()), knowledge_base_id=knowledge_base_id, document_key=document_key, file_name=file_name, source_path=source_path, file_type=file_type, file_size=file_size, content_hash=content_hash, category=category, permission_level=permission_level)
            session.add(document)
            await session.flush()
            event = OutboxEvent(id=str(uuid4()), event_type="knowledge.document_created", aggregate_type="knowledge_document", aggregate_id=str(document.id), payload_json={"document_id": str(document.id)}, status="pending")
            session.add(event)
            await session.commit()
            await session.refresh(document)
            return document, str(event.id)

    async def enqueue_ingestion(self, document_id: str) -> str:
        async with self.database.session_factory() as session:
            event = OutboxEvent(id=str(uuid4()), event_type="knowledge.document_created", aggregate_type="knowledge_document", aggregate_id=document_id, payload_json={"document_id": document_id}, status="pending")
            session.add(event)
            await session.commit()
            return str(event.id)

    async def list_documents(self, *, limit: int = 100, offset: int = 0) -> tuple[int, list[KnowledgeDocument]]:
        async with self.database.session_factory() as session:
            total = int((await session.execute(
                select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.deleted_at.is_(None))
            )).scalar_one())
            result = await session.execute(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.deleted_at.is_(None))
                .order_by(KnowledgeDocument.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return total, list(result.scalars().all())

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        try:
            UUID(document_id)
        except ValueError:
            return None
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, KnowledgeDocument.deleted_at.is_(None)))
            return result.scalar_one_or_none()

    async def active_chunk_embeddings_by_hash(self, *, document_id: str) -> dict[str, list[float]]:
        """Return reusable vectors from the currently active document version."""
        async with self.database.session_factory() as session:
            result = await session.execute(
                select(KnowledgeChunk.content_hash, KnowledgeChunk.embedding)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                .where(
                    KnowledgeChunk.document_id == document_id,
                    KnowledgeChunk.status == "active",
                    KnowledgeDocumentVersion.status == "active",
                    KnowledgeChunk.embedding.is_not(None),
                )
            )
            return {content_hash: embedding for content_hash, embedding in result.all() if embedding is not None}
    async def create_version(self, *, document_id: str, content_hash: str, parser_type: str) -> KnowledgeDocumentVersion:
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeDocumentVersion.version_no).where(KnowledgeDocumentVersion.document_id == document_id).order_by(KnowledgeDocumentVersion.version_no.desc()).limit(1))
            latest = result.scalar_one_or_none() or 0
            version = KnowledgeDocumentVersion(id=str(uuid4()), document_id=document_id, version_no=latest + 1, content_hash=content_hash, parser_type=parser_type)
            session.add(version)
            await session.commit()
            await session.refresh(version)
            return version

    async def save_chunks(self, *, document_id: str, version_id: str, chunks: list[dict]) -> int:
        async with self.database.session_factory() as session:
            rows = [KnowledgeChunk(id=str(uuid4()), document_id=document_id, version_id=version_id, chunk_index=int(chunk["chunk_index"]), content=chunk["content"], content_hash=chunk["content_hash"], title_path=chunk.get("title_path", ""), page_number=chunk.get("page_number"), metadata_json=chunk.get("metadata", {}), embedding=chunk.get("embedding")) for chunk in chunks]
            session.add_all(rows)
            await session.commit()
            return len(rows)

    async def activate_version(self, *, document_id: str, version_id: str, chunk_count: int) -> None:
        async with self.database.session_factory() as session:
            await session.execute(update(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id == document_id).values(status="superseded", deleted_at=None))
            await session.execute(update(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == version_id).values(status="active", chunk_count=chunk_count, activated_at=func.now()))
            await session.execute(update(KnowledgeDocument).where(KnowledgeDocument.id == document_id).values(status="active", current_version_id=version_id, error_message=None))
            await session.commit()

    async def mark_document_failed(self, *, document_id: str, version_id: str | None, error_message: str) -> None:
        async with self.database.session_factory() as session:
            await session.execute(update(KnowledgeDocument).where(KnowledgeDocument.id == document_id).values(status="failed", error_message=error_message[:4000]))
            if version_id:
                await session.execute(update(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == version_id).values(status="failed", error_message=error_message[:4000]))
            await session.commit()

    async def search_hybrid(self, *, query: str, query_embedding: list[float] | None, top_k: int, knowledge_base_id: str | None = None, category: str | None = None, scope: KnowledgeScope | None = None) -> list[tuple[KnowledgeChunk, float]]:
        async with self.database.session_factory() as session:
            base = select(KnowledgeChunk).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id).where(KnowledgeChunk.status == "active", KnowledgeDocument.status == "active", KnowledgeDocument.deleted_at.is_(None), KnowledgeDocumentVersion.status == "active")
            if knowledge_base_id:
                base = base.where(KnowledgeDocument.knowledge_base_id == knowledge_base_id)
            if category:
                base = base.where(KnowledgeDocument.category == category)
            text_rank = func.ts_rank_cd(func.to_tsvector("simple", KnowledgeChunk.content), func.websearch_to_tsquery("simple", query))
            lexical = func.coalesce(text_rank, literal(0.0))
            if query_embedding:
                semantic = (1.0 - KnowledgeChunk.embedding.cosine_distance(query_embedding))
                score = (literal(0.65) * semantic + literal(0.35) * lexical).label("score")
                statement = select(KnowledgeChunk, score).select_from(base.subquery()).order_by(score.desc()).limit(top_k)
                # Rebuild directly because selecting from an ORM subquery loses entity mapping.
                statement = select(KnowledgeChunk, score).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id).where(KnowledgeChunk.status == "active", KnowledgeDocument.status == "active", KnowledgeDocument.deleted_at.is_(None), KnowledgeDocumentVersion.status == "active", KnowledgeChunk.embedding.is_not(None))
                if knowledge_base_id:
                    statement = statement.where(KnowledgeDocument.knowledge_base_id == knowledge_base_id)
                if category:
                    statement = statement.where(KnowledgeDocument.category == category)
                statement = statement.order_by(score.desc()).limit(top_k)
            else:
                score = lexical.label("score")
                statement = select(KnowledgeChunk, score).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id).where(KnowledgeChunk.status == "active", KnowledgeDocument.status == "active", KnowledgeDocument.deleted_at.is_(None), KnowledgeDocumentVersion.status == "active").order_by(score.desc()).limit(top_k)
                if knowledge_base_id:
                    statement = statement.where(KnowledgeDocument.knowledge_base_id == knowledge_base_id)
                if category:
                    statement = statement.where(KnowledgeDocument.category == category)
            result = await session.execute(statement)
            return [(chunk, float(score or 0.0)) for chunk, score in result.all()]

    async def search_text(self, *, query: str, top_k: int, knowledge_base_id: str | None = None, category: str | None = None, scope: KnowledgeScope | None = None) -> list[KnowledgeChunk]:
        async with self.database.session_factory() as session:
            statement = select(KnowledgeChunk).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id).where(KnowledgeChunk.status == "active", KnowledgeDocument.status == "active", KnowledgeDocument.deleted_at.is_(None), KnowledgeDocumentVersion.status == "active")
            if knowledge_base_id:
                statement = statement.where(KnowledgeDocument.knowledge_base_id == knowledge_base_id)
            if category:
                statement = statement.where(KnowledgeDocument.category == category)
            terms = [term for term in query.replace("，", " ").replace("。", " ").split() if len(term) >= 2]
            if terms:
                statement = statement.where(or_(*[KnowledgeChunk.content.ilike(f"%{term}%") for term in terms]))
            else:
                statement = statement.where(KnowledgeChunk.content.ilike(f"%{query}%"))
            statement = statement.limit(top_k)
            result = await session.execute(statement)
            return list(result.scalars().all())
