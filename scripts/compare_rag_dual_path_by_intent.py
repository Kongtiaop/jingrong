#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""三路召回对比 v2（复用真实检索路径）+ normalize 增益诊断。

修正点：
1. hybrid 直接调真实 `KnowledgeRepository.search_hybrid`（= 生产双路 RRF + top_k），不再手写 RRF。
2. keyword_only 复用真实 `_rank_text_pg_search`（= 生产关键词腿），并分别用
   - 原 query（case["query"]）
   - normalize 后 query（normalize_customer_query）
   对比出 normalize 对 BM25 关键词腿是增益还是副作用（任务 6/C）。
3. vector_only 用真实 cosine 向量查询（= 生产向量腿）。

指标：fragment_recall / hit_rate，按 intent 分层（样本不均衡）。

用法（需 DB + DASHSCOPE key，fulltext_backend=pg_search）：
  python scripts/compare_rag_dual_path_by_intent.py
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict

from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion
from app.repositories.knowledge import KnowledgeRepository
from app.services.customer_rag_service import normalize_customer_query
from app.services.understanding_config import understanding_config

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
    base_top_k = understanding_config.get_top_k()   # 非动态缺省（供表头展示）
    cases = load_cases()
    agg = defaultdict(lambda: {"kn_rec": 0.0, "kn_hit": 0, "kw_rec": 0.0, "kw_hit": 0,
                               "v_rec": 0.0, "v_hit": 0, "h_rec": 0.0, "h_hit": 0,
                               "h_topk": 0, "n": 0})
    try:
        async with db.session_factory() as session:
            flt = await _filters()
            for case in cases:
                intent = case["intent"]
                gold = case["gold_fragments"]
                raw = case["query"]
                norm = normalize_customer_query(raw, intent)  # 生产文本腿用的同义词扩展串
                qvec = (await provider.embed([raw]))[0]  # 向量腿用原 query embedding（与生产一致）

                # keyword_only: 原 query（不进 normalize）
                kw_raw = await repo._rank_text_pg_search(session, query=raw, filters=flt, candidate_k=20)
                kraw_c = [c.content for c, _ in kw_raw]
                kn_rec, kn_hit = _fragment_recall(kraw_c, gold)

                # keyword_only: normalize 后 query（= 生产关键词腿实际吃到的）
                kw_norm = await repo._rank_text_pg_search(session, query=norm, filters=flt, candidate_k=20)
                knorm_c = [c.content for c, _ in kw_norm]
                kw_rec, kw_hit = _fragment_recall(knorm_c, gold)

                # vector_only: 真实 cosine 查询
                vdist = KnowledgeChunk.embedding.cosine_distance(qvec)
                vch = (await session.execute(
                    select(KnowledgeChunk).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                    .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id)
                    .where(*flt, KnowledgeChunk.embedding.is_not(None))
                    .order_by(vdist.asc()).limit(20))).scalars().all()
                v_c = [c.content for c in vch]
                v_rec, v_hit = _fragment_recall(v_c, gold)

                # hybrid: 复用真实 search_hybrid（生产双路 RRF + 动态 top_k）。
                # 与生产一致：文本腿吃 normalize 后 query，top_k 按 query 难度/枚举动态给定（A′），
                # 向量腿用 raw 的 embedding（search_hybrid 内部对 query 与 embedding 分腿用）。
                h_topk = understanding_config.get_top_k_for_query(raw)
                pairs = await repo.search_hybrid(query=norm, query_embedding=qvec, top_k=h_topk)
                hy_c = [c.content for c, _, _ in pairs]
                h_rec, h_hit = _fragment_recall(hy_c, gold)

                a = agg[intent]
                a["kn_rec"] += kn_rec; a["kn_hit"] += kn_hit
                a["kw_rec"] += kw_rec; a["kw_hit"] += kw_hit
                a["v_rec"] += v_rec; a["v_hit"] += v_hit
                a["h_rec"] += h_rec; a["h_hit"] += h_hit
                a["h_topk"] += h_topk
                a["n"] += 1
    finally:
        if provider:
            await provider.close()
        await db.dispose()

    print(f"== 三路召回 + normalize 诊断（base_top_k={base_top_k}，fragment_recall / hit_rate）==")
    print("kw-norm=关键词腿用normalize(生产实际); kw-raw=关键词腿用原query; vector; hybrid(真实search_hybrid+动态top_k)")
    header = f"{'intent':<18}{'n':>3}{'tk':>4}  {'kw-norm':>13}  {'kw-raw':>11}  {'vector':>11}  {'hybrid':>11}"
    print(header)
    tot = defaultdict(float)
    totn = 0
    for intent in sorted(agg, key=lambda k: -agg[k]["n"]):
        a = agg[intent]
        n = a["n"]
        totn += n
        for k in ("kn_rec", "kn_hit", "kw_rec", "kw_hit", "v_rec", "v_hit", "h_rec", "h_hit", "h_topk"):
            tot[k] += a[k]
        print(f"{intent:<18}{n:>3}{a['h_topk']/n:4.0f}  {a['kw_rec']/n*100:5.1f}%/{a['kw_hit']/n*100:4.0f}%  "
              f"{a['kn_rec']/n*100:5.1f}%/{a['kn_hit']/n*100:4.0f}%  "
              f"{a['v_rec']/n*100:5.1f}%/{a['v_hit']/n*100:4.0f}%  "
              f"{a['h_rec']/n*100:5.1f}%/{a['h_hit']/n*100:4.0f}%")
    print(f"{'ALL':<18}{totn:>3}{tot['h_topk']/totn:4.0f}  {tot['kw_rec']/totn*100:5.1f}%/{tot['kw_hit']/totn*100:4.0f}%  "
          f"{tot['kn_rec']/totn*100:5.1f}%/{tot['kn_hit']/totn*100:4.0f}%  "
          f"{tot['v_rec']/totn*100:5.1f}%/{tot['v_hit']/totn*100:4.0f}%  "
          f"{tot['h_rec']/totn*100:5.1f}%/{tot['h_hit']/totn*100:4.0f}%")
    print("\n说明：kw-norm vs kw-raw 的差 = normalize 对 BM25 关键词腿的净影响（负值=膨胀词有害）。")


if __name__ == "__main__":
    asyncio.run(main())
