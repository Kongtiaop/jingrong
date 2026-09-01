#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用当前 embedding 模型重嵌入 RAG 分块（修复"存储向量与查询向量不兼容"）。

诊断显示：存储 chunk 向量与当前查询向量虽然同维度(1024)但 cosine 相似度≈0（嵌入空间
不兼容，很可能是入库时用了旧/不同 embedding 模型）。此脚本读已存 chunk 内容，用当前
`QwenProvider.embed`（`text-embedding-v4`，与生产/docker 一致）重向量化并原地更新，无需源文件。

用法（需 DB 已跑 + DASHSCOPE key）：
  dry-run 看将重嵌入多少： python -m scripts.reembed_rag_chunks
  实际重嵌入：             python -m scripts.reembed_rag_chunks --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际更新 embedding（否则 dry-run）")
    a = ap.parse_args()

    settings = get_settings()
    if not settings.dashscope_api_key:
        print("无 DASHSCOPE_API_KEY。")
        return
    db = Database(settings)
    provider = QwenProvider(settings)
    try:
        async with db.session_factory() as s:
            # 与检索一致的可检索分块：document active + 未删 + version active + chunk active
            result = await s.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.content)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                .where(KnowledgeChunk.status == "active",
                       KnowledgeDocument.status == "active",
                       KnowledgeDocument.deleted_at.is_(None),
                       KnowledgeDocumentVersion.status == "active")
            )
            rows = list(result.all())
        print(f"可检索激活分块: {len(rows)} 个（将用当前模型 {settings.qwen_embedding_model} 重嵌入，dim={settings.embedding_dimension}）")
        if not a.apply:
            print("dry-run：未写入。加 --apply 实际重嵌入。")
            return

        vectors = {}
        for start in range(0, len(rows), 8):
            batch = rows[start:start + 8]
            emb = await provider.embed([c for _, c in batch])
            vectors.update({str(i): v for i, v in zip([r[0] for r in batch], emb)})

        async with db.session_factory() as s:
            for i, (cid, _) in enumerate(rows):
                cur = (await s.execute(select(KnowledgeChunk.metadata_json).where(KnowledgeChunk.id == cid))).scalar_one()
                meta = dict(cur or {})
                meta["embedding_model"] = settings.qwen_embedding_model
                await s.execute(KnowledgeChunk.__table__.update()
                                .where(KnowledgeChunk.id == cid)
                                .values(embedding=vectors[str(cid)], metadata_json=meta))
                if (i + 1) % 20 == 0:
                    await s.commit()
            await s.commit()
        print(f"已重嵌入 {len(rows)} 个分块（当前模型 {settings.qwen_embedding_model}）。")
        print("建议随后：python -m scripts.run_rag_eval 看检索质量是否回升。")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
