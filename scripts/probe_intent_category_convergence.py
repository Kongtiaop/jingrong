#!/usr/bin/env python
"""批量探针：意图 → 类目 的收敛情况与"类目生效后"的影响评估。

背景（来自容器实测，见 BUG_LOG ISSUE-20260914-INTENT-CATEGORY-FILTER-NEVER-APPLIED）：
  `CustomerRagService` 只传 scope、**从不传 category**；而
  `scope_for_customer_intent()` 对所有公开知识类意图返回**同一个含 6 类目的 scope**，
  于是 repository 的类目过滤 `category.in_(scope.categories)` **等价于不过滤**。
  实测：`company_business` intent 检索回 5 条**全部是 company_info 类目**的文档，
  即便扩大 top_k=25 也一条 company_business 都没有（而库中有 39 个该类目 chunk）。

本探针回答三个问题（全部基于**批量**数据，不用单条样本下结论）：
  1. 每个意图检索到的 chunk，**类目分布**是什么？（"自身类目命中率"）
  2. 若类目**真的生效**（模拟 category=自身类目 过滤），best_sim 与可作答性如何变化？
     —— 用于评估"过滤"方案的代价（会不会因收窄而检索不到东西）。
  3. 多意图之间，**类目生效后**是否才出现真实的信息增量？

用法（容器内）：
  python scripts/probe_intent_category_convergence.py
  python scripts/probe_intent_category_convergence.py --intents company_info company_business
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 覆盖公开知识类意图的代表性问句（每个意图至少 2 条，避免单样本）
DEFAULT_CASES: list[tuple[str, str]] = [
    ("company_info", "你们公司叫什么名字"),
    ("company_info", "公司客户规模有多大"),
    ("company_business", "你们公司的主营业务是什么"),
    ("company_business", "公司提供哪些服务"),
    ("policy", "什么是双录"),
    ("policy", "风险等级怎么划分"),
    ("workflow", "开户流程是怎样的"),
    ("workflow", "怎么修改绑定银行卡"),
    ("faq", "客服电话是多少"),
    ("faq", "怎么联系你们"),
]


async def _categories_for(session, chunk_ids: list[str]) -> list[str]:
    """批量取 chunk 所属文档类目（一次查询，避免逐条 round-trip）。"""
    if not chunk_ids:
        return []
    from sqlalchemy import text
    rows = (await session.execute(
        text("SELECT c.id AS cid, d.category AS cat FROM knowledge_chunk c "
             "JOIN knowledge_document d ON d.id = c.document_id "
             "WHERE c.id = ANY(:ids)"), {"ids": chunk_ids})).fetchall()
    by_id = {str(r.cid): str(r.cat) for r in rows}
    return [by_id.get(str(cid), "?") for cid in chunk_ids]


async def main() -> int:
    parser = argparse.ArgumentParser(description="意图→类目收敛批量探针")
    parser.add_argument("--intents", nargs="*", default=None,
                        help="只跑这些意图；默认覆盖公开知识类意图")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_rag_service import (
        CustomerRagService, normalize_customer_query, scope_for_customer_intent,
    )
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever

    cases = DEFAULT_CASES
    if args.intents:
        cases = [c for c in DEFAULT_CASES if c[0] in set(args.intents)]
    if not cases:
        print("没有可跑的用例", file=sys.stderr)
        return 2

    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    retriever = WealthKnowledgeRetriever(KnowledgeRepository(database))
    service = CustomerRagService(retriever, embedding_provider=provider)

    # ---- 语料规模（作为"命中率"的分母参照）----
    from sqlalchemy import text
    async with database.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT d.category AS cat, count(c.id) AS n FROM knowledge_document d "
            "JOIN knowledge_chunk c ON c.document_id = d.id "
            "GROUP BY d.category ORDER BY n DESC"))).fetchall()
    corpus = {str(r.cat): int(r.n) for r in rows}
    print("=" * 92)
    print(f"语料规模（chunk 数）: {corpus}")
    print(f"thresholds = {service.thresholds}   top_k = {args.top_k}")
    print("=" * 92)
    print()
    print(f"{'intent':<17}{'query':<22}{'现状类目分布':<28}{'自身命中':<9}"
          f"{'best':<8}{'收窄后类目':<20}{'best2':<8}")
    print("-" * 92)

    rows_out = []
    for intent, query in cases:
        normalized = normalize_customer_query(query, intent)
        emb = (await provider.embed([query]))[0]
        scope = scope_for_customer_intent(intent)

        # 现状：只带 scope（= 实际线上行为）
        hits = await retriever.search(query=normalized, query_embedding=emb,
                                      top_k=args.top_k, scope=scope)
        async with database.session_factory() as session:
            cats = await _categories_for(session, [str(h.chunk_id) for h in hits])
        dist = dict(Counter(cats))
        self_hit = cats.count(intent)
        sims = [float(h.vector_similarity) for h in hits if h.vector_similarity is not None]
        best = max(sims) if sims else None

        # 模拟"类目生效"：显式传 category=intent。
        # 注意：`WealthKnowledgeRetriever.search()` **本身支持 category 参数**
        # （只是 CustomerRagService 从不传它）——所以这里不需要任何退路/私有属性访问，
        # 直接传参即可，口径与线上完全一致。
        hits2 = await retriever.search(query=normalized, query_embedding=emb,
                                       top_k=args.top_k, scope=scope, category=intent)
        async with database.session_factory() as session:
            cats2 = await _categories_for(session, [str(h.chunk_id) for h in hits2])
        dist2 = dict(Counter(cats2))
        sims2 = [float(h.vector_similarity) for h in hits2 if h.vector_similarity is not None]
        best2 = max(sims2) if sims2 else None

        print(f"{intent:<17}{query:<22}{str(dist):<28}{f'{self_hit}/{len(cats)}':<9}"
              f"{(f'{best:.4f}' if best else '-'):<8}{str(dist2):<20}"
              f"{(f'{best2:.4f}' if best2 else '-'):<8}")
        rows_out.append((intent, query, dist, self_hit, len(cats), best, dist2, best2))

    print()
    print("=" * 92)
    print("汇总：自身类目命中率（现状 vs 收窄后）")
    print("=" * 92)
    by_intent: dict[str, list] = {}
    for r in rows_out:
        by_intent.setdefault(r[0], []).append(r)
    for intent, rs in sorted(by_intent.items()):
        now_hit = sum(r[3] for r in rs)
        now_tot = sum(r[4] for r in rs)
        rate = now_hit / now_tot if now_tot else 0.0
        narrowed = sum(1 for r in rs if r[6] and intent in r[6])
        print(f"  {intent:<18} 现状自身命中 {now_hit}/{now_tot} ({rate:.0%})   "
              f"收窄后有命中 {narrowed}/{len(rs)} 条用例   "
              f"（语料 {corpus.get(intent, 0)} chunks）")

    zero = [r for r in rows_out if r[5] is None]
    zero2 = [r for r in rows_out if r[7] is None]
    print()
    print(f"现状无相似度(检索空) 用例数 = {len(zero)}/{len(rows_out)}")
    print(f"收窄后无相似度(检索空) 用例数 = {len(zero2)}/{len(rows_out)}"
          f"{'  <- 收窄可能导致答不出，需评估' if len(zero2) > len(zero) else ''}")

    await provider.close()
    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
