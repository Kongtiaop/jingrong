#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAG 检索质量评测 runner（数据飞轮 · 检索参数闭环）。

对评测集每条 query 跑真实检索（`WealthKnowledgeRetriever` → pgvector `search_hybrid`），
按 gold_fragments 计算 hit / fragment_recall / evidence 指标；支持 --params 覆盖
top_k / rrf_k / min_score，用于参数网格对比。

用法（需 DB 已跑，含已入库的知识分块+向量）：
  用当前 config 参数跑全部：   python -m scripts.run_rag_eval
  指定参数组合：               python -m scripts.run_rag_eval --top_k 8 --rrf_k 100 --min_score 0.05
  只看 val（调优用）：          python -m scripts.run_rag_eval --split val
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from app.common.knowledge_scope import KnowledgeScope
from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.repositories.knowledge import KnowledgeRepository
from app.services.understanding_config import understanding_config
from app.services.knowledge_retrieval import WealthKnowledgeRetriever

EVAL_CASES = os.path.join(os.path.dirname(__file__), "rag_eval_cases.json")


def load_cases(path: str = EVAL_CASES) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["cases"]


def _fragment_recall(retrieved_contents: list[str], gold: list[str]) -> tuple[float, bool]:
    """gold 片段被 top-k 检索内容覆盖的比例；hit=任一命中。"""
    found = sum(1 for frag in gold if any(frag in content for content in retrieved_contents))
    n = len(gold) or 1
    return found / n, found > 0


async def _run_case(retriever: WealthKnowledgeRetriever, case: dict, *, top_k: int,
                    rrf_k: int, min_score: float, embedding_provider: QwenProvider | None = None) -> dict:
    scope = KnowledgeScope.public_customer()
    query_embedding = None
    if embedding_provider is not None:
        try:
            query_embedding = (await embedding_provider.embed([case["query"]]))[0]
        except Exception:  # noqa: BLE001 - embedding 失败则退回纯文本
            query_embedding = None
    citations = await retriever.search(query=case["query"], query_embedding=query_embedding,
                                       top_k=top_k, scope=scope, rrf_k=rrf_k)
    contents = [c.content for c in citations]
    recall, hit = _fragment_recall(contents, case["gold_fragments"])
    useful = [c for c in citations if c.score >= min_score]
    return {
        "id": case["id"], "intent": case["intent"], "query": case["query"],
        "top_k": top_k, "retrieved": len(citations),
        "mode": "hybrid" if query_embedding else "text",
        "recall": recall, "hit": hit,
        "evidence": len(useful) >= 1,
        "top_scores": [round(c.score, 4) for c in citations[:3]],
    }


async def run(cases: list[dict], *, top_k: int, rrf_k: int, min_score: float,
              database: Database, embedding_provider: QwenProvider | None = None) -> tuple[list[dict], dict]:
    repo = KnowledgeRepository(database)
    retriever = WealthKnowledgeRetriever(repo)
    results = [await _run_case(retriever, c, top_k=top_k, rrf_k=rrf_k, min_score=min_score,
                               embedding_provider=embedding_provider) for c in cases]
    n = len(results) or 1
    agg = {
        "n": len(results),
        "recall": sum(r["recall"] for r in results) / n,
        "hit_rate": sum(1 for r in results if r["hit"]) / n,
        "evidence_rate": sum(1 for r in results if r["evidence"]) / n,
        "avg_top_scores": sum((r["top_scores"][0] if r["top_scores"] else 0.0) for r in results) / n,
    }
    return results, agg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["all", "train", "val", "test"])
    ap.add_argument("--top_k", type=int, default=None)
    ap.add_argument("--rrf_k", type=int, default=None)
    ap.add_argument("--min_score", type=float, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cases = load_cases()
    if a.split != "all":
        cases = [c for c in cases if c.get("split", "train") == a.split]
        print(f"[split={a.split}] 用例 {len(cases)} 条")

    top_k = a.top_k if a.top_k is not None else understanding_config.get_top_k()
    rrf_k = a.rrf_k if a.rrf_k is not None else understanding_config.get_rrf_k()
    min_score = a.min_score if a.min_score is not None else understanding_config.get_min_score()
    settings = get_settings()
    provider = QwenProvider(settings) if settings.dashscope_api_key else None
    print(f"参数：top_k={top_k} rrf_k={rrf_k} min_score={min_score}  mode={'hybrid(有embedding)' if provider else 'text(无embedding)'}")

    database = Database(settings)
    asyncio.run(_run_database(cases, top_k, rrf_k, min_score, database, a.out, provider))


async def _run_database(cases, top_k, rrf_k, min_score, database, out, provider=None):
    try:
        results, agg = await run(cases, top_k=top_k, rrf_k=rrf_k, min_score=min_score, database=database, embedding_provider=provider)
    finally:
        await database.dispose()
    print(f"检索质量（n={agg['n']}）：fragment_recall={agg['recall']:.1%} hit_rate={agg['hit_rate']:.1%} evidence_rate={agg['evidence_rate']:.1%} avg_top1_score={agg['avg_top_scores']:.3f}")
    print("\n失败用例（未命中任一 gold 片段）：")
    for r in results:
        if not r["hit"]:
            print(f"  [{r['id']}][{r['intent']}] {r['query']} | recall={r['recall']:.2f} top_scores={r['top_scores']}")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"params": {"top_k": top_k, "rrf_k": rrf_k, "min_score": min_score}, "agg": agg, "results": results}, f, ensure_ascii=False, indent=2)
        print(f"\n已保存: {out}")


if __name__ == "__main__":
    main()
