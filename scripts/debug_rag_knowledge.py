#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断 RAG 知识库现状：文档/分块/向量/scope 覆盖。

用法（需 DB 已跑）：
  python -m scripts.debug_rag_knowledge
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.common.knowledge_scope import KnowledgeScope
from app.core.settings import get_settings
from app.db.session import Database
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion

PUBLIC_SCOPE = KnowledgeScope.public_customer()


async def main() -> None:
    db = Database(get_settings())
    try:
        async with db.session_factory() as s:
            # 知识库
            bases = (await s.execute(select(KnowledgeBase))).scalars().all()
            print(f"知识库 {len(bases)} 个: {[(b.name, b.status) for b in bases]}")

            docs = (await s.execute(select(KnowledgeDocument).where(KnowledgeDocument.deleted_at.is_(None)))).scalars().all()
            print(f"\n文档共 {len(docs)} 个（未删）:")
            by_cat: dict = {}
            for d in docs:
                key = (d.category, d.permission_level, d.status)
                by_cat[key] = by_cat.get(key, 0) + 1
                print(f"  id={d.id[:8]}  file={d.file_name}  category={d.category}  perm={d.permission_level}  status={d.status}")

            # 激活分块
            active_chunks = (await s.execute(
                select(func.count()).select_from(KnowledgeChunk)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                .where(KnowledgeChunk.status == "active", KnowledgeDocumentVersion.status == "active")
            )).scalar_one()
            # 有向量的分块
            embed_chunks = (await s.execute(
                select(func.count()).select_from(KnowledgeChunk)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                .where(KnowledgeChunk.status == "active", KnowledgeDocumentVersion.status == "active",
                       KnowledgeChunk.embedding.is_not(None))
            )).scalar_one()
            print(f"\n激活分块: {active_chunks}，其中有向量: {embed_chunks}")

            # 被 public_customer scope 覆盖的文档数
            covered = sum(1 for d in docs if PUBLIC_SCOPE.allows(knowledge_base_id=d.knowledge_base_id, category=d.category, permission_level=d.permission_level))
            print(f"public_customer scope 覆盖的文档: {covered}/{len(docs)}")
            print(f"  scope 要求 category∈{PUBLIC_SCOPE.categories} 且 perm∈{PUBLIC_SCOPE.permission_levels}")

            # 抽样内容
            sample = (await s.execute(
                select(KnowledgeChunk.content).join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                .where(KnowledgeChunk.status == "active", KnowledgeDocumentVersion.status == "active").limit(5)
            )).scalars().all()
            print(f"\n抽样激活分块内容（{len(sample)} 条）:")
            for c in sample:
                print(f"  {c[:60]!r}")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
