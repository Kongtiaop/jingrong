#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""直接诊断向量相似度：查询向量到所有激活 chunk 的余弦距离，绕过 search_hybrid。

定位"适当性指南等文档为何排不进 top"——是维度不匹配 / 向量质量问题 / 还是真不相似。

用法（需 DB 已跑 + DASHSCOPE key）：
  python -m scripts.debug_rag_vectors --query "什么是适当性管理" --top-n 10
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion


def _cos(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return float("nan")
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return float("nan")
    return 1.0 - dot / (na * nb)  # cosine distance


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--top-n", type=int, default=10)
    a = ap.parse_args()

    settings = get_settings()
    db = Database(settings)
    provider = QwenProvider(settings) if settings.dashscope_api_key else None
    if provider is None:
        print("无 DASHSCOPE key，无法算查询向量。")
        return

    qvec = (await provider.embed([a.query]))[0]
    print(f"查询向量维度: {len(qvec)}")

    rows = []
    async with db.session_factory() as s:
        result = await s.execute(
            select(KnowledgeChunk.content, KnowledgeChunk.embedding, KnowledgeDocument.file_name)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
            .where(KnowledgeChunk.status == "active", KnowledgeDocument.status == "active",
                   KnowledgeDocument.deleted_at.is_(None),
                   KnowledgeDocumentVersion.status == "active",
                   KnowledgeChunk.embedding.is_not(None))
        )
        rows = [(content, embedding, fname) for content, embedding, fname in result.all()]

    print(f"激活+有向量 chunk: {len(rows)} 个")
    dims = {len(e) for _, e, _ in rows}
    print(f"存储向量维度集合: {dims}")
    if len(dims) != 1 or len(qvec) not in dims:
        print(f"!! 维度不匹配：查询={len(qvec)}，存储={dims} —— 相似度无意义！")
        return

    scored = sorted(((_cos(qvec, e), fname, content) for content, e, fname in rows),
                    key=lambda x: x[0])
    print(f"\n余弦距离最小（最相似）的前 {a.top_n}:")
    for dist, fname, content in scored[:a.top_n]:
        print(f"  dist={dist:.4f}  doc={fname}")
        print(f"      {content[:80]!r}")
    # 统计各文档在最相似里的占比
    from collections import Counter
    top_docs = Counter(fname for _, fname, _ in scored[:a.top_n])
    print(f"\ntop{a.top_n} 文档分布: {dict(top_docs)}")
    # 找出适当性指南相关 chunk 的最近距离
    print("\n适当性/风险/指南相关 chunk 的余弦距离:")
    for dist, fname, content in scored:
        if any(k in content for k in ["适当性", "风险测评", "专业投资者"]):
            print(f"  dist={dist:.4f}  doc={fname}  {content[:60]!r}")


if __name__ == "__main__":
    asyncio.run(main())
