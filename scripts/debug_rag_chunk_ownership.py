#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断各文档的 chunk 归属：为什么有些文档（如适当性指南）不在可检索集。

按文档列出：状态 / 分类 / 权限 / 总 chunk / 激活版本 chunk / 激活+有向量 chunk /
是否进入"可检索集"（=document.active + 未删 + version.active + chunk.active + 有向量）。

用法（需 DB 已跑）：
  python -m scripts.debug_rag_chunk_ownership
"""
from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion


async def main() -> None:
    db = Database(get_settings())
    try:
        async with db.session_factory() as s:
            docs = (await s.execute(select(KnowledgeDocument).where(KnowledgeDocument.deleted_at.is_(None)))).scalars().all()
            for d in docs:
                # 该文档全部分块
                total = (await s.execute(
                    select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == d.id))).scalar_one()
                # 激活版本 + 激活分块
                active_ver = (await s.execute(
                    select(func.count()).select_from(KnowledgeChunk)
                    .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                    .where(KnowledgeChunk.document_id == d.id,
                           KnowledgeChunk.status == "active",
                           KnowledgeDocumentVersion.status == "active"))).scalar_one()
                # 激活版本 + 激活 + 有向量
                vec = (await s.execute(
                    select(func.count()).select_from(KnowledgeChunk)
                    .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                    .where(KnowledgeChunk.document_id == d.id,
                           KnowledgeChunk.status == "active",
                           KnowledgeDocumentVersion.status == "active",
                           KnowledgeChunk.embedding.is_not(None)))).scalar_one()
                retrievable = bool(vec) and d.status == "active" and d.deleted_at is None
                print(f"doc={d.file_name} status={d.status} cat={d.category} perm={d.permission_level}")
                print(f"   总chunk={total} 激活版本激活chunk={active_ver} 激活+有向量={vec} 可检索={'YES' if retrievable else 'NO'}")
                # 当前版本
                print(f"   current_version_id={d.current_version_id}")

            # 也看被删除的文档（可能是"重复文档挂走了激活 chunk"的元凶）
            deleted = (await s.execute(select(KnowledgeDocument).where(KnowledgeDocument.deleted_at.is_not(None)))).scalars().all()
            if deleted:
                print(f"\n被删除的文档 {len(deleted)} 个（可能仍挂着激活 chunk）：")
                for d in deleted:
                    chunks = (await s.execute(
                        select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.document_id == d.id))).scalar_one()
                    print(f"  {d.file_name} status={d.status} deleted_at={d.deleted_at} chunks={chunks}")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
