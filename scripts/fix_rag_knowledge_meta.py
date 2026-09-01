#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修正 RAG 知识元数据：让该进公共 scope 的文档被正确分类/设权限。

按 file_name 更新 KnowledgeDocument 的 category / permission_level，并对指定的
未激活文档跑入库（分块+向量+激活）。DB 已跑时执行。

用法：
  仅打印将改动的映射（dry-run）： python -m scripts.fix_rag_knowledge_meta
  实际更新分类/权限：            python -m scripts.fix_rag_knowledge_meta --apply
  更新后再入库指定文档：          python -m scripts.fix_rag_knowledge_meta --apply --ingest "个人投资者适当性管理指南.md"

默认映射（依据诊断）：
  - 个人投资者适当性管理指南.md : general → policy（public）—— 适当性/风险内容应属政策类、面向客户可见
  - 高净值客户服务规范.md       : 保持 company_business / staff —— 内部文档，不进公共 RAG（不改）
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select, update

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion
from app.repositories.knowledge import KnowledgeRepository
from app.services.knowledge_ingestion import KnowledgeIngestionService

# file_name -> {category, permission_level}（None=不修改）
FIXES = {
    "个人投资者适当性管理指南.md": {"category": "policy", "permission_level": "public"},
}


async def run(apply: bool, ingest: list[str]) -> None:
    db = Database(get_settings())
    try:
        async with db.session_factory() as s:
            docs = (await s.execute(select(KnowledgeDocument).where(KnowledgeDocument.deleted_at.is_(None)))).scalars().all()
            print(f"文档 {len(docs)} 个：")
            for d in docs:
                fix = FIXES.get(d.file_name)
                new_cat = fix["category"] if fix else d.category
                new_perm = fix["permission_level"] if fix else d.permission_level
                changed = (new_cat != d.category) or (new_perm != d.permission_level)
                tag = f"→ ({new_cat}, {new_perm})" if changed else ""
                print(f"  {d.file_name}: category={d.category} perm={d.permission_level} status={d.status} {tag}")

            if apply and FIXES:
                for d in docs:
                    fix = FIXES.get(d.file_name)
                    if not fix:
                        continue
                    await s.execute(update(KnowledgeDocument).where(KnowledgeDocument.id == d.id).values(
                        category=fix["category"], permission_level=fix["permission_level"]))
                await s.commit()
                print(f"\n已更新分类/权限：{list(FIXES.keys())}")

        # 修复"静默事故"：文档有激活+有向量的 chunk（在 active 版本里）但 status != active
        # （通常是被某次入库失败打成 failed），导致整个文档被检索静默排除。
        if apply:
            async with db.session_factory() as s:
                docs = (await s.execute(select(KnowledgeDocument).where(KnowledgeDocument.deleted_at.is_(None)))).scalars().all()
                fixed_status = []
                for d in docs:
                    if d.status == "active":
                        continue
                    vec = (await s.execute(
                        select(func.count()).select_from(KnowledgeChunk)
                        .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                        .where(KnowledgeChunk.document_id == d.id,
                               KnowledgeChunk.status == "active",
                               KnowledgeDocumentVersion.status == "active",
                               KnowledgeChunk.embedding.is_not(None)))).scalar_one()
                    if vec:
                        await s.execute(update(KnowledgeDocument).where(KnowledgeDocument.id == d.id).values(status="active"))
                        fixed_status.append((d.file_name, vec))
                await s.commit()
                if fixed_status:
                    print(f"\n已把以下文档 status 从 failed/processing 恢复为 active（有 {len(fixed_status)} 个激活+有向量 chunk）: {[f for f, _ in fixed_status]}")
                else:
                    print("\n无需要恢复 status 的文档。")

        # 入库（激活）指定文档
        if ingest:
            settings = get_settings()
            if not settings.dashscope_api_key:
                print("未配置 DASHSCOPE_API_KEY，无法入库（需向量化）。")
                return
            repo = KnowledgeRepository(db)
            svc = KnowledgeIngestionService(repo, QwenProvider(settings))
            async with db.session_factory() as s:
                for name in ingest:
                    d = (await s.execute(select(KnowledgeDocument).where(
                        KnowledgeDocument.file_name == name, KnowledgeDocument.deleted_at.is_(None)))).scalars().all()
                    for doc in d:
                        res = await svc.ingest_document(str(doc.id))
                        print(f"入库 {name}: chunks={res.chunk_count} embedded={res.embedded_chunks} reused={res.reused_chunks} status={res.status}")
    finally:
        await db.dispose()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际更新分类/权限（否则 dry-run）")
    ap.add_argument("--ingest", action="append", default=[], help="入库（激活）指定 file_name 的文档，可多次")
    a = ap.parse_args()
    asyncio.run(run(a.apply, a.ingest))


if __name__ == "__main__":
    main()
