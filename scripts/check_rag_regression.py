#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAG 检索质量回归门禁（数据飞轮 · 检索参数纳入治理闭环）。

检索参数改动后，在 **test 集**上跑 RAG 检索质量，对比存档基线；hit_rate 或 fragment_recall
低于基线（超容差）则判回归，退出码 1（拦截上线）。与 `check_intent_regression` 对应。

用法（需 DB 已跑 + 知识分块向量已入库）：
  已知好配置时存档基线：   python -m scripts.check_rag_regression --save-baseline
  改动后校验是否回归：     python -m scripts.check_rag_regression        （0=通过 / 1=回归）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.services.understanding_config import understanding_config
from scripts.run_rag_eval import load_cases, run

DEFAULT_BASELINE = os.path.join(os.path.dirname(__file__), "..", "logs", "rag_baseline_test.json")


async def _run_test() -> tuple[list[dict], dict]:
    test = [c for c in load_cases() if c.get("split", "train") == "test"]
    settings = get_settings()
    provider = QwenProvider(settings) if settings.dashscope_api_key else None
    database = Database(settings)
    try:
        _, agg = await run(test, top_k=understanding_config.get_top_k(),
                           rrf_k=understanding_config.get_rrf_k(),
                           min_score=understanding_config.get_min_score(),
                           database=database, embedding_provider=provider)
    finally:
        await database.dispose()
    return test, agg


def _load_baseline(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _fmt(agg: dict) -> str:
    return f"hit={agg['hit_rate']:.1%} recall={agg['recall']:.1%} evidence={agg['evidence_rate']:.1%} (n={agg['n']})"


def _regressed(base_agg: dict, cur_agg: dict, tol_hit: float, tol_recall: float) -> bool:
    _EPS = 1e-6  # 浮点误差
    d_hit = base_agg.get("hit_rate", 0) - cur_agg.get("hit_rate", 0)
    d_recall = base_agg.get("recall", 0) - cur_agg.get("recall", 0)
    return d_hit - _EPS > tol_hit or d_recall - _EPS > tol_recall


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--tolerance-hit", type=float, default=0.1, help="hit_rate 允许下降量（绝对百分点，如 0.1=10pp）")
    ap.add_argument("--tolerance-recall", type=float, default=0.1)
    a = ap.parse_args()

    _, agg = asyncio.run(_run_test())
    print(f"当前 test 检索质量：{_fmt(agg)}（参数 top_k={understanding_config.get_top_k()} rrf_k={understanding_config.get_rrf_k()} min_score={understanding_config.get_min_score()}）")

    if a.save_baseline:
        os.makedirs(os.path.dirname(a.baseline), exist_ok=True)
        payload = {"agg": agg, "params": {"top_k": understanding_config.get_top_k(),
                                          "rrf_k": understanding_config.get_rrf_k(),
                                          "min_score": understanding_config.get_min_score()}}
        with open(a.baseline, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"已存档 RAG 基线 → {a.baseline}")
        return

    base = _load_baseline(a.baseline)
    if base is None:
        print(f"无 RAG 基线（{a.baseline}）。请先在已知好配置下 --save-baseline。")
        raise SystemExit(2)

    base_agg = base.get("agg", {})
    print(f"基线   ：{_fmt(base_agg)}")
    d_hit = base_agg.get("hit_rate", 0) - agg["hit_rate"]
    d_recall = base_agg.get("recall", 0) - agg["recall"]
    print(f"Δ hit={-d_hit:+.1%}  Δ recall={-d_recall:+.1%}")
    regressed = _regressed(base_agg, agg, a.tolerance_hit, a.tolerance_recall)
    if regressed:
        print("[RAG回归门禁] [FAIL] 检索质量回归——不建议上线，请回滚检索参数或继续调优。")
        raise SystemExit(1)
    print("[RAG回归门禁] [PASS] 检索质量无回归，可放行。")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
