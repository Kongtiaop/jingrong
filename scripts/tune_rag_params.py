#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RAG 检索参数网格调优（数据飞轮 · 检索参数闭环）。

在 **val 集**上对 top_k/rrf_k/min_score 网格搜索，按 (hit_rate, fragment_recall, evidence_rate)
选最优组合，与当前 config 参数对比；`--apply-best` 可把更优参数写入 `config/understanding.yaml`
（自动落版本，可回滚）。

用法（需 DB 已跑）：
  只搜最优并打印：          python -m scripts.tune_rag_params
  自定义网格：              python -m scripts.tune_rag_params --grid-top-k "3,5,8" --grid-min-score "0.05,0.1"
  落地更优参数：            python -m scripts.tune_rag_params --apply-best
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import sys

from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.services.understanding_config import understanding_config
from scripts import config_versioning
from scripts.run_rag_eval import load_cases, run

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "understanding.yaml")


def _load_cfg() -> dict:
    import yaml
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_cfg(cfg: dict) -> None:
    import yaml
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def _key(agg: dict):
    return (agg["hit_rate"], agg["recall"], agg["evidence_rate"])


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid-top-k", default="3,5,8,10")
    ap.add_argument("--grid-rrf-k", default="30,60,100")
    ap.add_argument("--grid-min-score", default="0.05,0.1,0.2")
    ap.add_argument("--apply-best", action="store_true", help="把更优参数写入 config/understanding.yaml（自动落版本）")
    a = ap.parse_args()

    grid_top = [int(x) for x in a.grid_top_k.split(",") if x.strip()]
    grid_rrf = [int(x) for x in a.grid_rrf_k.split(",") if x.strip()]
    grid_ms = [float(x) for x in a.grid_min_score.split(",") if x.strip()]
    combos = list(itertools.product(grid_top, grid_rrf, grid_ms))
    print(f"val 集网格调优：{len(combos)} 组参数组合")

    val_cases = [c for c in load_cases() if c.get("split", "train") == "val"]
    print(f"val 用例 {len(val_cases)} 条")
    settings = get_settings()
    provider = QwenProvider(settings) if settings.dashscope_api_key else None
    database = Database(settings)
    scored = []
    try:
        for top_k, rrf_k, min_score in combos:
            _, agg = await run(val_cases, top_k=top_k, rrf_k=rrf_k, min_score=min_score,
                               database=database, embedding_provider=provider)
            scored.append(((top_k, rrf_k, min_score), agg))
    finally:
        await database.dispose()

    ranked = sorted(scored, key=lambda x: _key(x[1]), reverse=True)
    print("\n参数组合排名（按 hit/recall/evidence）：")
    for (top_k, rrf_k, min_score), agg in ranked:
        print(f"  top_k={top_k:<3} rrf_k={rrf_k:<4} min_score={min_score:<4} | hit={agg['hit_rate']:.1%} recall={agg['recall']:.1%} evidence={agg['evidence_rate']:.1%}")

    best_params, best_agg = ranked[0]
    cur = (understanding_config.get_top_k(), understanding_config.get_rrf_k(), understanding_config.get_min_score())
    print(f"\n最优：top_k={best_params[0]} rrf_k={best_params[1]} min_score={best_params[2]}（hit={best_agg['hit_rate']:.1%} recall={best_agg['recall']:.1%}）")
    print(f"当前：top_k={cur[0]} rrf_k={cur[1]} min_score={cur[2]}")

    if a.apply_best:
        cfg = _load_cfg()
        ret = cfg.setdefault("retrieval", {})
        if (ret.get("top_k") == best_params[0] and ret.get("rrf_k") == best_params[1]
                and abs(float(ret.get("min_score", -1)) - best_params[2]) < 1e-9):
            print("最优参数与当前一致，无需落地。")
            return
        pre = config_versioning.snapshot(label="rag_param_pre_apply", changes=["RAG 参数调优前（回滚点）"])
        ret["top_k"] = best_params[0]
        ret["rrf_k"] = best_params[1]
        ret["min_score"] = best_params[2]
        _save_cfg(cfg)
        post = config_versioning.snapshot(label="rag_param_apply", changes=[f"top_k={best_params[0]} rrf_k={best_params[1]} min_score={best_params[2]}"])
        print(f"已写入 RAG 参数到 config/understanding.yaml（改动前=v{pre}、后=v{post}）")
        print(f"如需回滚：python -m scripts.config_version rollback {pre}")
        print("建议随后跑 test 回归：python -m scripts.check_intent_regression")


if __name__ == "__main__":
    asyncio.run(main())
