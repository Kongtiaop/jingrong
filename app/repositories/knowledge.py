from __future__ import annotations

import re
from uuid import UUID, uuid4

from sqlalchemy import Float, func, literal, or_, select, text, update

from app.db.session import Database
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion
from app.models.outbox import OutboxEvent
from app.common.knowledge_scope import KnowledgeScope
from app.core.settings import get_settings
from app.services.understanding_config import understanding_config


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

    async def list_ingestion_events(self, *, document_id: str, limit: int = 20) -> list[OutboxEvent]:
        async with self.database.session_factory() as session:
            result = await session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "knowledge.document_created",
                    OutboxEvent.aggregate_id == document_id,
                )
                .order_by(OutboxEvent.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

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

    async def active_chunk_embeddings_by_hash(self, *, document_id: str, embedding_model: str | None = None) -> dict[str, list[float]]:
        """Return reusable vectors from the currently active document version.

        仅返回可复用的向量（有值）；若给定 embedding_model，只返回**用同一模型**算出的
        向量——避免 embedding 模型变化后增量入库静默复用不兼容的旧向量。
        """
        async with self.database.session_factory() as session:
            stmt = (
                select(KnowledgeChunk.content_hash, KnowledgeChunk.embedding)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                .where(
                    KnowledgeChunk.document_id == document_id,
                    KnowledgeChunk.status == "active",
                    KnowledgeDocumentVersion.status == "active",
                    KnowledgeChunk.embedding.is_not(None),
                )
            )
            if embedding_model:
                stmt = stmt.where(KnowledgeChunk.metadata_json["embedding_model"].astext == embedding_model)
            result = await session.execute(stmt)
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

    async def search_hybrid(self, *, query: str, query_embedding: list[float] | None, top_k: int, knowledge_base_id: str | None = None, category: str | None = None, scope: KnowledgeScope | None = None, rrf_k: int | None = None) -> list[tuple[KnowledgeChunk, float, float | None]]:
        """Retrieve independent vector/text candidate lists and fuse them with RRF.

        返回 (chunk, rrf_score, best_cosine_similarity)。rrf_score 是排名融合值（相关/无关都约 0.016），
        不适合当"证据置信度"；best_cosine_similarity（1-余弦距离，无向量时 None）用于拒答/转人工判定。
        """
        candidate_k = max(top_k * 4, 20)
        rrf_k = rrf_k if rrf_k is not None else understanding_config.get_rrf_k()
        async with self.database.session_factory() as session:
            filters = [
                KnowledgeChunk.status == "active",
                KnowledgeDocument.status == "active",
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocumentVersion.status == "active",
            ]
            if knowledge_base_id:
                filters.append(KnowledgeDocument.knowledge_base_id == knowledge_base_id)
            if category:
                filters.append(KnowledgeDocument.category == category)
            if scope:
                if scope.knowledge_base_ids:
                    filters.append(KnowledgeDocument.knowledge_base_id.in_(scope.knowledge_base_ids))
                if scope.categories:
                    filters.append(KnowledgeDocument.category.in_(scope.categories))
                if scope.permission_levels:
                    filters.append(KnowledgeDocument.permission_level.in_(scope.permission_levels))

            text_rows = await self._rank_text_branch(
                session, query=query, filters=filters, candidate_k=candidate_k,
            )
            vector_rows = []
            if query_embedding:
                vector_distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
                vector_statement = (
                    select(KnowledgeChunk, vector_distance.label("dist"))
                    .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                    .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                    .where(*filters, KnowledgeChunk.embedding.is_not(None))
                    .order_by(vector_distance.asc(), KnowledgeChunk.id.asc())
                    .limit(candidate_k)
                )
                vector_rows = [(chunk, float(dist)) for chunk, dist in (await session.execute(vector_statement)).all()]

            by_id = {str(chunk.id): chunk for chunk, _ in text_rows}
            for chunk, _ in vector_rows:
                by_id[str(chunk.id)] = chunk
            text_rank_by_id = {str(chunk.id): rank for rank, (chunk, _) in enumerate(text_rows, start=1)}
            vector_rank_by_id = {str(chunk.id): rank for rank, (chunk, _) in enumerate(vector_rows, start=1)}
            vector_sim_by_id = {str(chunk.id): max(0.0, 1.0 - dist) for chunk, dist in vector_rows}
            fused = []
            for chunk_id, chunk in by_id.items():
                rrf = 0.0
                if chunk_id in text_rank_by_id:
                    rrf += 1.0 / (rrf_k + text_rank_by_id[chunk_id])
                if chunk_id in vector_rank_by_id:
                    rrf += 1.0 / (rrf_k + vector_rank_by_id[chunk_id])
                fused.append((chunk, rrf, vector_sim_by_id.get(chunk_id)))
            fused.sort(key=lambda item: (-item[1], str(item[0].id)))
            return fused[:top_k]

    async def _rank_text_tsvector(self, session, *, query: str, filters: list, candidate_k: int) -> list:
        """关键词腿：PostgreSQL 内置全文检索（ts_rank_cd）。现状默认后端。"""
        text_rank = func.ts_rank_cd(
            func.to_tsvector("simple", KnowledgeChunk.content),
            func.websearch_to_tsquery("simple", query),
        )
        text_statement = (
            select(KnowledgeChunk, text_rank.label("rank"))
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
            .where(*filters)
            .order_by(text_rank.desc(), KnowledgeChunk.id.asc())
            .limit(candidate_k)
        )
        rows = list((await session.execute(text_statement)).all())
        # 中文全文检索在 'simple' 配置下不切词，ts_rank 多为 0 → 那些"噪声"文本结果
        # 会以固定顺序拉满候选池、拖垮 RRF 融合（相关 chunk 反而排不进 top-k）。
        # 只保留真正有全文命中的文本结果（rank>0）；中文整句无命中则文本分支退空→纯向量。
        return [row for row in rows if (row.rank or 0) > 0]

    # 常见中文疑问/停用词，避免 BM25 关键词腿被无信息量的词污染（影响召回纯度与分数）。
    _PG_SEARCH_STOPWORDS = frozenset({
        "什么", "怎么", "如何", "哪些", "哪个", "为什么", "是不是", "是否", "多少", "怎么查",
        "是什么", "为什么", "应该", "可以", "需要", "请问", "的", "了", "和", "与", "及", "或", "在",
        "对", "给", "我", "你", "他", "这", "那", "一下", "大概", "里面", "相关", "上",
        "是", "吗", "呢", "吧", "啊", "呀", "哪", "么", "怎", "什", "能", "会", "有", "要", "想", "买",
    })

    @classmethod
    def _extract_pg_search_terms(cls, query: str) -> str:
        """把整句中文明拆成可召回的检索词，空格连接（pg_search 空格默认 OR）。

        BM25 是词级/子串检索：整句中文不切分会命不中（'什么是适当性管理'→0），而较长中文
        短语若含停用词开头/结尾也可能整串不匹配。做法：
          1) 按中文标点/空白切成若干片段；
          2) 丢弃命中停用词的整段（'什么/是/怎么'等无信息量词）；
          3) 对每个剩余片段：长度>=5 的中文片段生成**字 bigram**（重叠2字窗，如
             '适当性管理'->'适当 当性 性管 管理'），利用 pg_search 对连续中文子串/词的匹配
             保证召回；短片段(2-4字)直接保留；纯字母数字(编号/代码)按原文保留；
          4) 去重后空格连接（pg_search 空格默认 OR 召回命中任一词的 chunk）。

        说明：这是启发式中文拆词（Python 侧未引入 jieba），目标是**保证关键词召回**；
        语义排序由向量腿承担，关键词腿只管精确命中与 RRF 融合。可用性已在真实库验证
        （'适当性'->12，bigram OR 召回相关 chunk）。
        """
        text = re.sub(r"[\s,，。；;：:、！!？?（()）\[\]【】“”\"'‘’…—-]+", " ", query)
        raw_segments = [seg.strip() for seg in text.split(" ") if seg.strip()]
        tokens: list[str] = []
        for seg in raw_segments:
            if not seg or seg in cls._PG_SEARCH_STOPWORDS:
                continue
            # 纯字母/数字/代码：按原文保留（如产品编号、风险代码 C1 等）
            if re.fullmatch(r"[A-Za-z0-9._/-]+", seg):
                tokens.append(seg)
                continue
            # 判断是否主要中文（含中文字符）
            cjk = [ch for ch in seg if "\u4e00" <= ch <= "\u9fff"]
            if not cjk:
                continue
            # 中文片段：循环剥除首/尾命中的停用词（含多字词，如'什么/怎么/是不是'等），避免污染
            stripped = seg
            changed = True
            while changed:
                changed = False
                for stop in sorted(cls._PG_SEARCH_STOPWORDS, key=len, reverse=True):
                    if stop and stripped.startswith(stop):
                        stripped = stripped[len(stop):]
                        changed = True
                        break
                for stop in sorted(cls._PG_SEARCH_STOPWORDS, key=len, reverse=True):
                    if stop and stripped.endswith(stop) and len(stripped) > len(stop):
                        stripped = stripped[:-len(stop)]
                        changed = True
                        break
            if not stripped or stripped in cls._PG_SEARCH_STOPWORDS:
                continue
            # 长度>=5 生成字 bigram；否则保留原片段
            if len(stripped) >= 5:
                bigrams = [stripped[i:i + 2] for i in range(len(stripped) - 1)]
                tokens.extend(b for b in bigrams if len(b) == 2 and b not in cls._PG_SEARCH_STOPWORDS)
            else:
                tokens.append(stripped)
        # 去重保序
        seen: set[str] = set()
        deduped: list[str] = []
        for term in tokens:
            if term not in seen:
                seen.add(term)
                deduped.append(term)
        return " ".join(deduped)

    async def _rank_text_pg_search(self, session, *, query: str, filters: list, candidate_k: int) -> list:
        """关键词腿：pg_search（ParadeDB BM25）。默认关闭、需装扩展并按版本核对 SQL。

        把整句中文明拆成关键词（空格分隔 = pg_search 默认 OR），对这批关键词做 BM25 召回，
        命中含任一词的可检索 chunk。形态与 tsvector 分支一致供 RRF 融合。

        事务隔离：本方法用**独立连接**执行 pg_search 原始查询，绝不在调用方 session 上直接跑
        `@@@`——否则 pg_search 扩展/索引缺失导致的 SQL 失败会中止(abort)调用方事务，进而污染
        后续 tsvector/vector 降级查询（InFailedSQLTransactionError）。任何异常由 _rank_text_branch
        捕获并降级回 tsvector。
        """
        from sqlalchemy import text as _sql_text

        q = self._extract_pg_search_terms(query)
        if not q:
            return []

        # 1) 在调用方 session 内做只读 scope 过滤，取出可检索 chunk 主键（不涉及 pg_search）。
        scoped_stmt = (
            select(KnowledgeChunk.id)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
            .where(*filters)
        )
        scoped_ids = [str(row.id) for row in (await session.execute(scoped_stmt)).all()]
        if not scoped_ids:
            return []
        raw = _sql_text(
            "SELECT id AS chunk_id FROM knowledge_chunk "
            "WHERE id = ANY(:ids) AND content @@@ :q "
            "ORDER BY paradedb.score(id) DESC LIMIT :limit"
        )
        # 2) pg_search BM25 查询在独立连接上执行；出错只回滚该连接，不影响调用方 session。
        async with self.database.engine.connect() as conn:
            await conn.rollback()  # 无挂起事务
            hits = (await conn.execute(raw, {"ids": scoped_ids, "q": q, "limit": candidate_k})).all()
        ids = [str(row.chunk_id) for row in hits]
        if not ids:
            return []
        # 3) 回调用方 session 按命中主键加载 ORM 对象（只读）。
        chunk_rows = (
            await session.execute(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))
        ).scalars().all()
        by_id = {str(c.id): c for c in chunk_rows}
        ordered = [by_id[chunk_id] for chunk_id in ids if chunk_id in by_id]
        return [(chunk, 1.0) for chunk in ordered]  # 形态 (chunk, rank)；RRF 只用枚举序

    async def _rank_text_branch(self, session, *, query: str, filters: list, candidate_k: int) -> list:
        """按 fulltext_backend 选关键词后端；pg_search 异常时降级回 tsvector。"""
        backend = get_settings().fulltext_backend.lower()
        if backend == "pg_search":
            try:
                rows = await self._rank_text_pg_search(session, query=query, filters=filters, candidate_k=candidate_k)
                if rows:
                    return rows
            except Exception as exc:  # noqa: BLE001 - 降级不阻塞检索
                from app.common.logging.config import get_logger
                get_logger(__name__).warning("pg_search_fulltext_failed_falling_back_to_tsvector err=%s", type(exc).__name__)
        return await self._rank_text_tsvector(session, query=query, filters=filters, candidate_k=candidate_k)

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
