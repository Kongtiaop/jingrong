#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断单条查询的检索结果：打印 top-k 命中的 chunk 内容与来源文档。

定位"相关文档为什么排不进 top-5 / 中文检索为什么命中差"。

用法（需 DB 已跑 + DASHSCOPE key）：
  python -m scripts.debug_rag_retrieval --query "什么是适当性管理" --top-k 5
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.common.knowledge_scope import KnowledgeScope
from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.models.knowledge import KnowledgeDocument
from app.repositories.knowledge import KnowledgeRepository
from app.services.knowledge_retrieval import WealthKnowledgeRetriever


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--scope", default="public", choices=["public", "all"])
    a = ap.parse_args()

    settings = get_settings()
    db = Database(settings)
    repo = KnowledgeRepository(db)
    retriever = WealthKnowledgeRetriever(repo)
    provider = QwenProvider(settings) if settings.dashscope_api_key else None

    # 文档 id -> file_name，便于标识来源
    doc_names = {}
    async with db.session_factory() as s:
        rows = (await s.execute(select(KnowledgeDocument.id, KnowledgeDocument.file_name))).all()
        doc_names = {str(i): n for i, n in rows}

    scope = None if a.scope == "all" else KnowledgeScope.public_customer()
    query_embedding = None
    if provider is not None:
        try:
            query_embedding = (await provider.embed([a.query]))[0]
        except Exception as e:  # noqa: BLE001
            print(f"embedding 失败: {type(e).__name__}: {e}")
    mode = "hybrid" if query_embedding else "text"
    print(f"query={a.query}  mode={mode}  scope={a.scope}  top_k={a.top_k}")

    citations = await retriever.search(query=a.query, query_embedding=query_embedding, top_k=a.top_k, scope=scope)
    print(f"\n检索到 {len(citations)} 条:")
    for i, c in enumerate(citations, 1):
        doc = doc_names.get(c.document_id, c.document_id)
        print(f"\n[{i}] score={c.score:.4f}  doc={doc}")
        print(f"    {c.content[:200]!r}")


if __name__ == "__main__":
    asyncio.run(main())
