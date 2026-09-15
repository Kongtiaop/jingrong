#!/usr/bin/env python
"""实验：若向量腿改用各 intent 自己的 normalized 嵌入，检索结果会分化吗？

背景（ISSUE-20260914-INTENT-EXPANSION-DOES-NOT-DIVERGE-RETRIEVAL）：
  当前实现：
      query_embedding = embed([query])          # 两个 intent 用**同一个**原始 query
      retriever.search(query=normalized_query,  # 只有文本腿不同
                       query_embedding=query_embedding)
  => 向量腿（决定排序的主力）对两个 intent 数学上必然相同；
     文本腿差异只占 36% 词且为通用词 -> 无法分化。
  实测 company_info 与 company_business 的 chunk_ids/similarities 逐字节相同。

本实验对照两种口径（其余条件完全一致）：
  口径「现状」：向量腿嵌入 = 原始 query（两个 intent 相同）
  口径「分化」：向量腿嵌入 = 各自的 normalized_query
对同一问题下的多个 intent 分别检索，比较：
  1. chunk 集合是否分化（Jaccard / 各自独有数）
  2. best_sim 是否变化（是否会跨过 answer=0.65 阈值，影响拒答门控）
并**跨多组问题**取样，避免单样本下结论。

用法（容器内）：
  python scripts/experiment_intent_vector_divergence.py
  python scripts/experiment_intent_vector_divergence.py --queries "你们公司的主营业务是什么" "公司提供哪些服务"
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 覆盖多个"多知识意图"问题，避免单样本。
# 注意：规划器对多数问句只给 1 个 intent（实测 5 条里仅 1 条给出 2 个），
# 若只依赖规划器配对，样本会退化成单条 —— 故本实验**不依赖规划器配对**，
# 而是对每条问句都**强制对比 company_info 与 company_business 两个意图**
# （这正是我们关心的一对），这样每组问题都能提供有效样本。
DEFAULT_QUERIES = [
    "你们公司的主营业务是什么",
    "公司提供哪些服务",
    "你们公司叫什么名字",
    "公司客户规模有多大",
    "公司有哪些金融牌照",
    "公司的经营范围是什么",
]

# 固定对比的意图对（当前唯一存在"检索不分化"问题的一对）
COMPARE_INTENTS = ["company_info", "company_business"]

TOP_K = 5


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


async def main() -> int:
    parser = argparse.ArgumentParser(description="向量腿分化实验")
    parser.add_argument("--queries", nargs="*", default=None)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    args = parser.parse_args()
    queries = args.queries or DEFAULT_QUERIES

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_intent import CustomerIntentPlanner
    from app.services.customer_rag_service import (
        normalize_customer_query, scope_for_customer_intent,
    )
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever

    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    retriever = WealthKnowledgeRetriever(KnowledgeRepository(database))

    print("=" * 104)
    print("意图分化三口径对照（目标：分化但不放宽门控）")
    print("=" * 104)
    print("  口径① 现状      : 向量腿=原始query嵌入  scope=6类目全开")
    print("  口径② 改嵌入源  : 向量腿=各自normalized  scope=6类目全开  <- 已测：分化但相似度虚高")
    print("  口径③ scope分化 : 向量腿=原始query嵌入  scope=各自类目    <- 本次重点")
    print("  门控口径恒定：一律用**原始 query 嵌入**算相似度，保证三口径可比、且不被膨胀词抬高")
    print("=" * 104)

    summary: list[tuple] = []
    for query in queries:
        plan = CustomerIntentPlanner().plan(query)
        planned = [t.intent for t in plan.tasks]
        intents = COMPARE_INTENTS
        print(f"\nquery = {query!r}")
        print(f"  规划器实际输出 = {planned}   （本实验固定对比 {intents}）")
        emb_raw = (await provider.embed([query]))[0]

        per_mode: dict[str, dict[str, dict]] = {"①现状": {}, "②改嵌入源": {}, "③scope分化": {}}
        for intent in intents:
            normalized = normalize_customer_query(query, intent)
            base_scope = scope_for_customer_intent(intent)
            emb_normalized = (await provider.embed([normalized]))[0]

            variants = {
                "①现状": (emb_raw, base_scope),
                "②改嵌入源": (emb_normalized, base_scope),
            }
            # ③ 检索范围收窄到自身类目，但**门控嵌入仍是原始 query**
            from app.common.knowledge_scope import KnowledgeScope
            narrowed = KnowledgeScope(
                knowledge_base_ids=base_scope.knowledge_base_ids,
                categories=(intent,),
                permission_levels=base_scope.permission_levels,
            )
            variants["③scope分化"] = (emb_raw, narrowed)

            for mode, (vec, scope) in variants.items():
                hits = await retriever.search(
                    query=normalized, query_embedding=vec, top_k=args.top_k, scope=scope)
                ids = {str(h.chunk_id) for h in hits}
                # 门控相似度：三口径一律用**原始 query 嵌入**重新计算，
                # 避免把"检索用哪个嵌入"与"门控用哪个嵌入"混为一谈。
                # 这样比较的是"分化效果"，而不是"相似度被抬高的副作用"。
                hits_raw = await retriever.search(
                    query=normalized, query_embedding=emb_raw, top_k=args.top_k, scope=scope)
                sims_raw = [float(h.vector_similarity) for h in hits_raw
                            if h.vector_similarity is not None]
                sims_used = [float(h.vector_similarity) for h in hits
                             if h.vector_similarity is not None]
                per_mode[mode][intent] = {
                    "ids": ids,
                    "best": max(sims_raw) if sims_raw else None,      # 门控口径（恒定）
                    "best_used": max(sims_used) if sims_used else None,  # 该口径实际返回的相似度
                    "n": len(hits),
                }

        for mode in per_mode:
            print(f"  --- 口径「{mode}」")
            for intent in intents:
                d = per_mode[mode][intent]
                print(f"      [{intent:<18}] 命中={d['n']}  "
                      f"门控best={d['best'] if d['best'] else 0:.4f}  "
                      f"ids={sorted(x[:8] for x in d['ids'])}")
                if d["n"] < args.top_k:
                    print(f"          **警告：命中不足 top_k（{d['n']}<{args.top_k}）"
                          f"-> 该口径下候选池可能过小，收窄有'检索不到'的风险**")

        for mode in per_mode:
            for a, b in itertools.combinations(intents, 2):
                da, db = per_mode[mode][a], per_mode[mode][b]
                inter = da["ids"] & db["ids"]
                jac = _jaccard(da["ids"], db["ids"])
                only_a, only_b = len(da["ids"] - db["ids"]), len(db["ids"] - da["ids"])
                tag = "完全重合" if jac == 1.0 else ("部分分化" if jac > 0 else "完全分化")
                print(f"  [{mode}] {a} vs {b}: Jaccard={jac:.3f} "
                      f"独有={only_a}/{only_b} 共有={len(inter)}  -> {tag}")
                summary.append((query, mode, a, b, jac, only_a, only_b,
                                da["best"], db["best"]))

        # 门控影响：门控相似度**恒定用原始 query**，故此处比较的是
        # "该口径实际返回的相似度是否被抬高"（②会抬高、①③不会）
        print(f"  --- 相似度是否被抬高（门控阈值 0.65）")
        for mode in per_mode:
            for intent in intents:
                d = per_mode[mode][intent]
                gate, used = d["best"], d["best_used"]
                flag = ""
                if gate is not None and used is not None and used - gate > 0.02:
                    flag = "  <== 被抬高（门控会被绕过）"
                print(f"      [{mode}] {intent:<18} 门控口径={gate if gate else 0:.4f}  "
                      f"实际返回={used if used else 0:.4f}{flag}")

    print()
    print("=" * 104)
    print("汇总（多条问题取平均，避免单样本结论）")
    print("=" * 104)
    for mode in ("①现状", "②改嵌入源", "③scope分化"):
        rows = [s for s in summary if s[1] == mode]
        if not rows:
            continue
        avg_jac = sum(r[4] for r in rows) / len(rows)
        fully_same = sum(1 for r in rows if r[4] == 1.0)
        diverged = sum(1 for r in rows if r[4] < 1.0)
        print(f"  口径「{mode:<12}」配对 {len(rows)} 组  平均 Jaccard={avg_jac:.3f}  "
              f"完全重合 {fully_same}  分化 {diverged}")

    print()
    print("判读：")
    print("  - 分化效果看 Jaccard 降幅（越低越分化）")
    print("  - 门控安全看“实际返回相似度”是否比门控口径虚高（②会虚高）")
    print("  理想口径 = Jaccard 低 且 相似度不虚高")

    await provider.close()
    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
