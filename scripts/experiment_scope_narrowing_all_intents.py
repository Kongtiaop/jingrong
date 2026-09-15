#!/usr/bin/env python
"""全意图 scope 收窄评估：哪些意图可以安全收窄、哪些会退化成"检索不到"。

目的（承接 experiment_intent_vector_divergence.py）：
  该实验证明 **scope 收窄** 是"分化意图且不抬高门控"的正确路径
  （company_info vs company_business：Jaccard 1.000 -> 0.000，且相似度零虚高；
   而"改嵌入源"虽也分化，却在 6 组里 5 组把相似度抬高、会绕过门控）。
  但在改 `scope_for_customer_intent()` 之前，必须知道**每个公开知识意图**收窄后的后果——
  否则可能出现"company_info/company_business 好了，workflow 却再也答不出来"。

对每个意图，本脚本输出：
  1. 收窄后**命中数**（是否 >= top_k；不足即"候选池过小"风险）
  2. 收窄后 **best_sim** vs 现状（是否有意图"崩掉"）
  3. 该意图**自身类目下的语料规模**（收窄后能看到的池子有多大）
  4. 两两意图之间的**分化程度**（Jaccard）

门控恒等：一律用**原始 query 嵌入**计算相似度，故不会把"虚高"混进结论。

用法（容器内）：
  python scripts/experiment_scope_narrowing_all_intents.py
  python scripts/experiment_scope_narrowing_all_intents.py --intents policy faq
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

# 每个意图的代表性问句。**全部由 planner 实测反查得出**，不是凭直觉写的。
#
# 实测发现（重要，解释了为何初版大量 MISMATCH）：
#   - `policy` 意图**没有任何问句能触发**（测了 31 条，"什么是双录/适当性/风险等级"
#     分别落到 unknown / faq / risk_public）-> 本实验无法评估其收窄效果，只能标注 N/A
#   - `workflow` / `risk_public` 各只有 1 条命中问句 -> 样本天然只有 1 条
#   - 大量自然问句落到 `unknown`（如"客服电话是多少"）
#   这本身就是值得记录的现象：意图规则的**触发面比预期窄得多**。
PROBES: dict[str, list[str]] = {
    "company_info": ["你们公司叫什么名字", "公司客户规模有多大"],
    "company_business": ["你们公司的主营业务是什么", "公司业务范围是什么"],
    "faq": ["怎么联系你们", "怎么修改绑定的银行卡"],
    "workflow": ["开户流程是怎样的"],            # 实测仅此 1 条
    "risk_public": ["风险等级怎么划分"],          # 实测仅此 1 条
    "policy": [],                                # 实测无问句可触发 -> 无法评估
}
TOP_K = 5


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


async def main() -> int:
    parser = argparse.ArgumentParser(description="全意图 scope 收窄评估")
    parser.add_argument("--intents", nargs="*", default=None)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    args = parser.parse_args()

    intents = args.intents or list(PROBES.keys())

    from sqlalchemy import text

    from app.common.knowledge_scope import KnowledgeScope
    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.repositories.knowledge import KnowledgeRepository
    from app.services.customer_rag_service import (
        normalize_customer_query, scope_for_customer_intent,
    )
    from app.services.knowledge_retrieval import WealthKnowledgeRetriever

    settings = get_settings()
    database = Database(settings)
    provider = QwenProvider(settings)
    retriever = WealthKnowledgeRetriever(KnowledgeRepository(database))

    # 各类目实际语料规模（判断"收窄后池子有多大"的前提）
    async with database.session_factory() as session:
        rows = (await session.execute(text(
            "SELECT d.category AS cat, count(c.id) AS n FROM knowledge_document d "
            "LEFT JOIN knowledge_chunk c ON c.document_id = d.id "
            "WHERE d.permission_level IN ('public','customer_visible') "
            "GROUP BY d.category ORDER BY n DESC"))).fetchall()
    corpus = {str(r.cat): int(r.n) for r in rows}

    print("=" * 104)
    print("全意图 scope 收窄评估（门控恒用原始 query 嵌入，不会虚高）")
    print("=" * 104)
    print(f"客户可见语料规模: {corpus}")
    print(f"top_k = {args.top_k}\n")

    def narrowed_scope(intent: str, base) -> KnowledgeScope:
        return KnowledgeScope(knowledge_base_ids=base.knowledge_base_ids,
                              categories=(intent,),
                              permission_levels=base.permission_levels)

    print(f"{'intent':<18}{'query':<24}{'现状命中':<9}{'现状best':<10}"
          f"{'收窄命中':<9}{'收窄best':<10}{'判定'}")
    print("-" * 104)

    per_intent_ids: dict[str, set] = {}
    verdicts: list[tuple] = []
    for intent in intents:
        probes = PROBES.get(intent, [])
        if not probes:
            print(f"{intent:<18}{'(无问句可触发)':<24}{'-':<9}{'-':<10}{'-':<9}{'-':<10}"
                  f"无法评估：planner 实测无任何问句归到该意图")
            continue
        for query in probes:
            normalized = normalize_customer_query(query, intent)
            emb = (await provider.embed([query]))[0]
            base = scope_for_customer_intent(intent)
            narrow = narrowed_scope(intent, base)

            wide_hits = await retriever.search(query=normalized, query_embedding=emb,
                                               top_k=args.top_k, scope=base)
            narrow_hits = await retriever.search(query=normalized, query_embedding=emb,
                                                 top_k=args.top_k, scope=narrow)

            def best(hits):
                sims = [float(h.vector_similarity) for h in hits
                        if h.vector_similarity is not None]
                return max(sims) if sims else None

            wb, nb = best(wide_hits), best(narrow_hits)
            n_narrow = len(narrow_hits)

            if n_narrow == 0:
                verdict = "**收窄后检索为空**（不可收窄）"
            elif n_narrow < args.top_k:
                verdict = f"候选池偏小（{n_narrow}<{args.top_k}）"
            elif nb is not None and wb is not None and nb < wb - 0.10:
                verdict = "相似度明显下降（需人工确认）"
            else:
                verdict = "OK"

            print(f"{intent:<18}{query:<24}{len(wide_hits):<9}"
                  f"{(f'{wb:.4f}' if wb else '-'):<10}{n_narrow:<9}"
                  f"{(f'{nb:.4f}' if nb else '-'):<10}{verdict}")
            verdicts.append((intent, query, len(wide_hits), wb, n_narrow, nb, verdict))
            per_intent_ids.setdefault(intent, set()).update(
                str(h.chunk_id) for h in narrow_hits)

    print()
    print("=" * 104)
    print("汇总：各意图收窄后的安全性")
    print("=" * 104)
    for intent in intents:
        vs = [v for v in verdicts if v[0] == intent]
        if not vs:
            continue
        empty = sum(1 for v in vs if v[4] == 0)
        small = sum(1 for v in vs if 0 < v[4] < args.top_k)
        corpus_n = corpus.get(intent, 0)
        print(f"  {intent:<18} 语料={corpus_n:<5} 用例={len(vs)}  "
              f"收窄后为空={empty}  候选池偏小={small}  "
              f"{'>> 不建议收窄' if empty else ('>> 谨慎' if small else '>> 可收窄')}")

    print()
    print("=" * 104)
    print("意图间分化程度（收窄口径下的 Jaccard，越低越分化）")
    print("=" * 104)
    active = [i for i in intents if per_intent_ids.get(i)]
    for a, b in itertools.combinations(active, 2):
        jac = _jaccard(per_intent_ids[a], per_intent_ids[b])
        print(f"  {a:<18} vs {b:<18} Jaccard={jac:.3f}")
    if not active:
        print("  （没有可比较的意图）")

    print()
    print("=" * 104)
    print("结论要点")
    print("=" * 104)
    empty_intents = [i for i in intents
                     if any(v[0] == i and v[4] == 0 for v in verdicts)]
    if empty_intents:
        print(f"  **以下意图收窄后检索为空，不可收窄**: {empty_intents}")
        print("   原因通常是其类目下没有客户可见语料（如 workflow / risk_public 此前实测为 0）。")
        print("   处置：这些意图应保持全开 scope，或先补语料再收窄。")
    print("  可安全收窄的意图：见上表标记 '可收窄' 者。")
    print("  建议落地方式：**按意图分别设定 scope**，而不是一刀切全部收窄——")
    print("    对语料充足的意图收窄（拿到分化收益），对语料为空的意图保持全开（避免答不出）。")

    await provider.close()
    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
