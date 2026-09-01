#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复杂中文意图评测 runner（数据飞轮 Phase 0）。

用法：
  规则路径基线（默认，确定性、免费）：
    conda run -n langchain_env python -m scripts.run_intent_eval --mode rules
  LLM 路径（需 DASHSCOPE_API_KEY，按案例计费）：
    conda run -n langchain_env python -m scripts.run_intent_eval --mode llm

输出：意图匹配率、澄清匹配率、完全准确率 + 失败用例清单；可 --out 存 JSON 供飞轮回流。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["cases"]


def _plan_to_intents(plan) -> tuple[list[str], bool]:
    intents = [t.intent for t in plan.tasks]
    clarify = any(t.intent == "unknown" for t in plan.tasks) or bool(getattr(plan, "clarification", None))
    return intents, clarify


def run_rules(cases: list[dict]) -> list[dict]:
    from app.services.customer_intent import CustomerIntentPlanner
    planner = CustomerIntentPlanner()
    results = []
    for case in cases:
        plan = planner.plan(case["query"])
        actual_intents, actual_clarify = _plan_to_intents(plan)
        exp = case["expected"]
        exp_intents = set(exp["intents"])
        results.append({
            "id": case["id"], "category": case["category"], "query": case["query"],
            "expected": exp["intents"], "actual": actual_intents,
            "expected_clarify": exp["requires_clarification"], "actual_clarify": actual_clarify,
            "intent_match": exp_intents <= set(actual_intents),
            "clarify_match": exp["requires_clarification"] == actual_clarify,
        })
    return results


async def run_llm(cases: list[dict]) -> list[dict]:
    from app.core.settings import get_settings
    from app.infrastructure.qwen import QwenProvider
    from app.services.llm_intent_fallback import LLMIntentFallbackService
    settings = get_settings()
    if not settings.dashscope_api_key:
        print("DASHSCOPE_API_KEY 未配置，无法跑 LLM 模式。", file=sys.stderr)
        raise SystemExit(1)
    service = LLMIntentFallbackService(QwenProvider(settings))
    results = []
    for case in cases:
        try:
            structured = await service.classify(query=case["query"])
            intents = [t.intent for t in structured.tasks] or [structured.primary_intent]
            clarify = structured.requires_clarification
        except Exception as exc:  # noqa: BLE001
            intents, clarify = ["error"], True
        exp = case["expected"]
        exp_intents = set(exp["intents"])
        results.append({
            "id": case["id"], "category": case["category"], "query": case["query"],
            "expected": exp["intents"], "actual": intents,
            "expected_clarify": exp["requires_clarification"], "actual_clarify": clarify,
            "intent_match": exp_intents <= set(intents),
            "clarify_match": exp["requires_clarification"] == clarify,
        })
    return results


def report(results: list[dict]) -> None:
    n = len(results) or 1
    intent_ok = sum(1 for r in results if r["intent_match"])
    clarify_ok = sum(1 for r in results if r["clarify_match"])
    both_ok = sum(1 for r in results if r["intent_match"] and r["clarify_match"])
    print(f"总用例: {len(results)}")
    print(f"意图匹配率: {intent_ok / n:.1%} ({intent_ok}/{len(results)})")
    print(f"澄清匹配率: {clarify_ok / n:.1%} ({clarify_ok}/{len(results)})")
    print(f"完全准确率: {both_ok / n:.1%} ({both_ok}/{len(results)})")
    print("\n失败用例:")
    for r in results:
        if not (r["intent_match"] and r["clarify_match"]):
            print(f"  [{r['id']}][{r['category']}] {r['query']} | 期望={r['expected']} 实际={r['actual']} | 澄清期望={r['expected_clarify']} 实际={r['actual_clarify']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="scripts/intent_eval_cases.json")
    ap.add_argument("--mode", default="rules", choices=["rules", "llm"])
    ap.add_argument("--split", default="all", choices=["all", "train", "val", "test"],
                    help="train=诊断/调优；val=筛选候选；test=最终验证；all=全部")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    cases = load_cases(a.cases)
    if a.split != "all":
        cases = [c for c in cases if c.get("split", "train") == a.split]
        print(f"[split={a.split}] 用例 {len(cases)} 条")
    if a.mode == "rules":
        results = run_rules(cases)
    else:
        results = asyncio.run(run_llm(cases))
    report(results)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump({"split": a.split, "results": results}, f, ensure_ascii=False, indent=2)
        print(f"\n已保存: {a.out}")


if __name__ == "__main__":
    main()
