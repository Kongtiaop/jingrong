#!/usr/bin/env python
"""为什么两个 knowledge intent 检索结果逐字节相同？—— 分离文本腿与向量腿。

已确认事实（容器实测）：
  company_info     normalized = "... 公司名称 公司介绍 联系方式 客户数量"
  company_business normalized = "... 公司业务 公司服务 服务范围 客户"
  两条检索结果 chunk_ids / similarities **完全相同**（Jaccard=1.000）
  命中类目分布也相同 {'company_business':2,'company_info':2,'faq':1}

两个 intent 只有 normalized_query 尾部扩展词不同。故要问：
  1. 文本腿（pg_search BM25）对这两个串分别返回什么？是否为空/是否相同？
  2. 向量腿（原始 query 嵌入）对两者是否相同？（应当相同——嵌入的是同一个 query）
  3. RRF 融合后相同，是不是因为**文本腿的差异被向量腿压过去**，
     或者文本腿其实返回空（中文拆词失败/被停用词过滤掉）？

本脚本分别调用 _rank_text_pg_search 与向量腿，打印各自的排名，定位差异消失在哪一步。

用法（容器内）：
  python scripts/diagnose_intent_query_effect.py --query "你们公司的主营业务是什么"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main() -> int:
    parser = argparse.ArgumentParser(description="定位 intent 扩展词为何不影响检索结果")
    parser.add_argument("--query", required=True)
    args = parser.parse_args()

    from sqlalchemy import text

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_rag_service import (
        normalize_customer_query, scope_for_customer_intent,
    )

    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    repo = KnowledgeRepository(database)

    print(f"FULLTEXT_BACKEND = {getattr(settings, 'fulltext_backend', '?')}")
    print(f"query = {args.query!r}\n")

    intents = ["company_info", "company_business"]
    emb = (await provider.embed([args.query]))[0]

    for intent in intents:
        normalized = normalize_customer_query(args.query, intent)
        scope = scope_for_customer_intent(intent)
        print("=" * 90)
        print(f"[{intent}]")
        print(f"  normalized = {normalized}")
        print(f"  拆词结果   = {repo._extract_pg_search_terms(normalized)!r}")
        async with database.session_factory() as session:
            # 复刻 search_hybrid 的 filters
            from app.models.knowledge import (
                KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion,
            )
            filters = [
                KnowledgeChunk.status == "active",
                KnowledgeDocument.status == "active",
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocumentVersion.status == "active",
            ]
            if scope.categories:
                filters.append(KnowledgeDocument.category.in_(scope.categories))
            if scope.permission_levels:
                filters.append(KnowledgeDocument.permission_level.in_(scope.permission_levels))

            text_rows = await repo._rank_text_pg_search(
                session, query=normalized, filters=filters, candidate_k=20)
            print(f"  文本腿命中 = {len(text_rows)}")
            for rank, (chunk, _score) in enumerate(text_rows[:5], 1):
                print(f"      #{rank} {str(chunk.id)[:8]} {chunk.content[:40]!r}")

            # 向量腿
            from app.models.knowledge import KnowledgeChunk as KC
            dist = KC.embedding.cosine_distance(emb)
            from sqlalchemy import select
            stmt = (select(KC, dist.label("d"))
                    .join(KnowledgeDocument, KnowledgeDocument.id == KC.document_id)
                    .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KC.version_id)
                    .where(*filters, KC.embedding.is_not(None))
                    .order_by(dist.asc(), KC.id.asc()).limit(20))
            vrows = (await session.execute(stmt)).all()
            print(f"  向量腿命中 = {len(vrows)}")
            for rank, (chunk, d) in enumerate(vrows[:5], 1):
                print(f"      #{rank} {str(chunk.id)[:8]} sim={1-d:.4f} {chunk.content[:40]!r}")

    await provider.close()
    await database.dispose()
    print()
    print("=" * 90)
    print("判读")
    print("=" * 90)
    print("  - 若两 intent 的**文本腿相同或皆为空** -> 扩展词没起作用（拆词后差异词被过滤/召不回）")
    print("  - 若文本腿不同但最终结果相同   -> 差异被向量腿压过（RRF 融合权重问题）")
    print("  - 若向量腿相同（必然，因嵌入的是同一 query）且文本腿为空 -> 结果必然相同")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
