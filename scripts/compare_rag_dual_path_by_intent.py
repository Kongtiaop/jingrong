#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""三路召回对比（按 intent 分层）：只向量 / 只关键词(BM25) / 双路(RRF)。

样本按 intent 不均衡，单独看整体会掩盖差异，故按 intent 分组输出
fragment_recall 与 hit_rate。
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import Counter, defaultdict

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion
from app.repositories.knowledge import KnowledgeRepository

EVAL_CASES = os.path.join(os.path.dirname(__file__), "rag_eval_cases.json")


def load_cases(path: str = EVAL_CASES) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["cases"]


def _fragment_recall(contents: list[str], gold: list[str]) -> tuple[float, bool]:
    found = sum(1 for frag in gold if any(frag in content for content in contents))
    n = len(gold) or 1
    return found / n, found > 0


async def _filters():
    return [
        KnowledgeChunk.status == "active",
        KnowledgeDocument.status == "active",
        KnowledgeDocument.deleted_at.is_(None),
        KnowledgeDocumentVersion.status == "active",
        KnowledgeDocument.category.in_(["company_info", "company_business", "faq", "workflow", "policy", "risk_public"]),
        KnowledgeDocument.permission_level.in_(["public", "customer_visible"]),
    ]


async def main() -> None:
    settings = get_settings()
    db = Database(settings)
    provider = QwenProvider(settings) if settings.dashscope_api_key else None
    if provider is None:
        print("no DASHSCOPE key")
        return
    repo = KnowledgeRepository(db)
    cases = load_cases()
    # 按 intent 聚合：vector_only / keyword_only / hybrid
    agg = defaultdict(lambda: {"v_rec": 0.0, "v_hit": 0, "k_rec": 0.0, "k_hit": 0,
                               "h_rec": 0.0, "h_hit": 0, "n": 0})
    try:
        async with db.session_factory() as session:
            flt = await _filters()
            rrf_k = 60
            for case in cases:
                intent = case["intent"]
                gold = case["gold_fragments"]
                qvec = (await provider.embed([case["query"]]))[0]
                # 关键词腿
                kw_rows = await repo._rank_text_pg_search(session, query=case["query"], filters=flt, candidate_k=20)
                kw_c = [c.content for c, _ in kw_rows]
                k_rec, k_hit = _fragment_recall(kw_c, gold)
                # 向量腿
                vdist = KnowledgeChunk.embedding.cosine_distance(qvec)
                vch = (await session.execute(
                    select(KnowledgeChunk).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                    .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                    .where(*flt, KnowledgeChunk.embedding.is_not(None))
                    .order_by(vdist.asc()).limit(20))).scalars().all()
                v_c = [c.content for c in vch]
                v_rec, v_hit = _fragment_recall(v_c, gold)
                # 双路 RRF top5
                by_id = {str(c.id): c for c, _ in kw_rows}
                for c in vch:
                    by_id.setdefault(str(c.id), c)
                krank = {str(c.id): i + 1 for i, (c, _) in enumerate(kw_rows)}
                vrank = {str(c.id): i + 1 for i, c in enumerate(vch)}
                fused = []
                for cid, c in by_id.items():
                    s = 0.0
                    if cid in krank:
                        s += 1.0 / (rrf_k + krank[cid])
                    if cid in vrank:
                        s += 1.0 / (rrf_k + vrank[cid])
                    fused.append((c, s))
                fused.sort(key=lambda x: x[1], reverse=True)
                hy_c = [c.content for c, _ in fused[:5]]
                h_rec, h_hit = _fragment_recall(hy_c, gold)

                a = agg[intent]
                a["v_rec"] += v_rec; a["v_hit"] += v_hit
                a["k_rec"] += k_rec; a["k_hit"] += k_hit
                a["h_rec"] += h_rec; a["h_hit"] += h_hit
                a["n"] += 1

    finally:
        if provider:
            await provider.close()
        await db.dispose()

    print("== 按 intent 三路召回对比 (fragment_recall / hit_rate) ==")
    header = f"{'intent':<18}{'n':>3}  {'vector':>12}  {'keyword':>12}  {'hybrid':>12}"
    print(header)
    for intent in sorted(agg, key=lambda k: -agg[k]["n"]):
        a = agg[intent]
        n = a["n"]
        print(f"{intent:<18}{n:>3}  {a['v_rec']/n*100:5.1f}%/{a['v_hit']/n*100:4.0f}%  "
              f"{a['k_rec']/n*100:5.1f}%/{a['k_hit']/n*100:4.0f}%  "
              f"{a['h_rec']/n*100:5.1f}%/{a['h_hit']/n*100:4.0f}%")
    # 整体
    tot = {"v_rec": 0.0, "v_hit": 0, "k_rec": 0.0, "k_hit": 0, "h_rec": 0.0, "h_hit": 0, "n": 0}
    for a in agg.values():
        for k in tot:
            tot[k] += a[k]
    n = tot["n"]
    print(f"{'ALL':<18}{n:>3}  {tot['v_rec']/n*100:5.1f}%/{tot['v_hit']/n*100:4.0f}%  "
          f"{tot['k_rec']/n*100:5.1f}%/{tot['k_hit']/n*100:4.0f}%  "
          f"{tot['h_rec']/n*100:5.1f}%/{tot['h_hit']/n*100:4.0f}%")


if __name__ == "__main__":
    asyncio.run(main())
