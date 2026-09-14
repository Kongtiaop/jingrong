# -*- coding: utf-8 -*-
"""多次采样：验证检索候选集是否在边界抖动（而非单次采样下结论）。用完即删。"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

QUERY = "你们公司的主营业务是什么"
INTENT = "company_info"
ROUNDS = 8


async def main() -> None:
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

    normalized = normalize_customer_query(QUERY, INTENT)
    scope = scope_for_customer_intent(INTENT)

    print(f"重复 {ROUNDS} 次，观察 chunk 集合 / best_sim 是否稳定\n")
    seen_sets: dict[frozenset, int] = {}
    bests: list[float] = []
    for i in range(1, ROUNDS + 1):
        emb = (await provider.embed([QUERY]))[0]
        hits = await retriever.search(query=normalized, query_embedding=emb, top_k=5, scope=scope)
        ids = frozenset(str(h.chunk_id)[:8] for h in hits)
        sims = [h.vector_similarity for h in hits if h.vector_similarity is not None]
        best = max(sims) if sims else 0.0
        bests.append(best)
        seen_sets[ids] = seen_sets.get(ids, 0) + 1
        print(f"  #{i}  best_sim={best:.4f}  ids={sorted(ids)}")

    print()
    print(f"不同 chunk 集合数 = {len(seen_sets)}（1 表示稳定）")
    for ids, n in sorted(seen_sets.items(), key=lambda kv: -kv[1]):
        print(f"  出现 {n} 次: {sorted(ids)}")
    print()
    print(f"best_sim: min={min(bests):.4f} max={max(bests):.4f} 极差={max(bests)-min(bests):.4f}")
    thr_lo, thr_hi = 0.35, 0.65
    in_band = [b for b in bests if thr_lo <= b < thr_hi]
    print(f"落在 judge 带 [{thr_lo},{thr_hi}) 的次数 = {len(in_band)}/{len(bests)}")
    if len(seen_sets) > 1:
        print("=> 检索候选集**不稳定**：集合类指标（重叠率）不能用单次采样判断")
    else:
        print("=> 本次采样内候选集稳定")

    await provider.close()
    await database.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop())
    except TypeError:
        asyncio.run(main())
