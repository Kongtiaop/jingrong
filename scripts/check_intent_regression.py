#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据飞轮闭环：test 集回归门禁。

任何理解层配置改动后，跑本脚本校验 **test 集**（最终验证、不参与调优）是否相比存档基线
发生回归。回归则退出码 1，用于 CI/人工门控，阻止把改坏的配置上线。

用法：
  首次/好配置时存档基线：   python -m scripts.check_intent_regression --save-baseline
  改动后校验是否回归：      python -m scripts.check_intent_regression            （退出码 0=通过 / 1=回归）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

EVAL_CASES = os.path.join(os.path.dirname(__file__), "intent_eval_cases.json")
DEFAULT_BASELINE = os.path.join(os.path.dirname(__file__), "..", "logs", "intent_baseline_test.json")


def _score(results: list[dict]) -> dict:
    n = len(results) or 1
    intent = sum(1 for r in results if r["intent_match"])
    clarify = sum(1 for r in results if r["clarify_match"])
    both = sum(1 for r in results if r["intent_match"] and r["clarify_match"])
    return {"full": both / n, "intent": intent / n, "clarify": clarify / n, "n": len(results)}


def _fmt(s: dict) -> str:
    return f"完全{s['full']:.1%} 意图{s['intent']:.1%} 澄清{s['clarify']:.1%} (n={s['n']})"


async def _run_test_split() -> list[dict]:
    from scripts.run_intent_eval import load_cases, run_llm
    cases = load_cases(EVAL_CASES)
    test = [c for c in cases if c.get("split", "train") == "test"]
    return await run_llm(test)


def _load_baseline(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--save-baseline", action="store_true", help="跑 test 集并存为基线（不判回归）")
    ap.add_argument("--tolerance-cases", type=int, default=1,
                    help="允许的完全准确失败数上浮量，超过即判回归")
    a = ap.parse_args()

    results = asyncio.run(_run_test_split())
    cur = _score(results)
    print(f"当前 test 集：{_fmt(cur)}")

    if a.save_baseline:
        os.makedirs(os.path.dirname(a.baseline), exist_ok=True)
        with open(a.baseline, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        print(f"已存档基线 → {a.baseline}")
        return

    base = _load_baseline(a.baseline)
    if base is None:
        print(f"无基线（{a.baseline}）。请先在已知好配置下跑 --save-baseline 存档基线。")
        raise SystemExit(2)

    # 以"条数"为单位比较（n 相同），更直观：完全准确少算的条数
    n = base.get("n") or cur["n"]
    base_full_ok = round(base["full"] * n)
    cur_full_ok = round(cur["full"] * n)
    base_intent_ok = round(base["intent"] * n)
    cur_intent_ok = round(cur["intent"] * n)
    drop_full = base_full_ok - cur_full_ok
    drop_intent = base_intent_ok - cur_intent_ok
    print(f"基线   {_fmt(base)}")
    print(f"当前   {_fmt(cur)}")
    print(f"Δ 完全 {cur['full'] - base['full']:+.1%}（少算 {drop_full} 条）  Δ 意图 {cur['intent'] - base['intent']:+.1%}（少算 {drop_intent} 条）")

    regressed = drop_full > a.tolerance_cases or drop_intent > a.tolerance_cases
    if regressed:
        print(f"[回归门禁] [FAIL] test 集发生回归（超出容差 {a.tolerance_cases} 条）——不建议上线，请回滚理解层配置或继续调优。")
        raise SystemExit(1)
    print("[回归门禁] [PASS] test 集无回归，可放行。")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
