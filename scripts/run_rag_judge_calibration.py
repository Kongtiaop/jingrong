#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM-as-judge 人工校准：算 judge 与人的一致性（Cohen's kappa）。

"谁来监督监督的人"——人是 judge 的最终裁判。用小样本：LLM-judge 打分 vs 人工打分，
算 kappa，判定 judge 是否可信（kappa≥0.6 才敢规模化）。

用法：
  1) 让 LLM-judge 打分并导出：  python -m scripts.run_rag_judge_calibration --mode judge
  2) 人工按同一细则给同样用例打分，填进 logs/rag_judge_human_scores.json
     （格式 [{"id":"ans_01","faithfulness":5,"relevancy":4,"correctness":5}, ...]）
  3) 算 kappa：                python -m scripts.run_rag_judge_calibration --mode calibrate
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from app.core.settings import get_settings
from app.infrastructure.qwen import QwenProvider
from scripts.run_rag_llm_judge import JUDGE_SYSTEM, judge_prompt, score_judge_result

CASES = os.path.join(os.path.dirname(__file__), "rag_answer_eval_cases.json")
LLM_SCORES = os.path.join(os.path.dirname(__file__), "..", "logs", "rag_judge_llm_scores.json")
HUMAN_SCORES = os.path.join(os.path.dirname(__file__), "..", "logs", "rag_judge_human_scores.json")


def cohens_kappa(a: list, b: list) -> float:
    """Cohen's kappa（二分类 pass/fail 用）。a/b 为同长 0/1 列表。"""
    n = len(a)
    if n == 0:
        return 1.0
    if len(a) != len(b):
        raise ValueError("评分长度不一致")
    obs = sum(1 for x, y in zip(a, b) if x == y) / n
    p_a = sum(a) / n
    p_b = sum(b) / n
    p_e = p_a * p_b + (1 - p_a) * (1 - p_b)
    if p_e == 1:
        return 1.0
    return (obs - p_e) / (1 - p_e) if obs != p_e else 1.0


def _as_binary(scores: list[dict], metric: str, threshold: int = 3) -> list[int]:
    return [1 if s.get(metric, 0) >= threshold else 0 for s in scores]


async def run_judge() -> None:
    settings = get_settings()
    if not settings.dashscope_api_key:
        print("无 DASHSCOPE_API_KEY。")
        return
    cases = json.load(open(CASES, encoding="utf-8"))["cases"]
    # 跳过拒答用例（judge 只评有答案的）
    cases = [c for c in cases if not c.get("requires_rejection")]
    provider = QwenProvider(settings)
    out = []
    try:
        for c in cases:
            # 简化：用 gold_answer 作为"答案"评 judge 一致性（真实场景应喂系统生成的答案）
            resp = await provider._client.chat.completions.create(
                model=provider.settings.qwen_max_model,
                temperature=0.0,
                messages=[{"role": "system", "content": JUDGE_SYSTEM},
                          {"role": "user", "content": judge_prompt(c["query"], [c.get("gold_answer", "")], "（待评答案）", c.get("gold_answer", ""))}],
            )
            scores = score_judge_result(resp.choices[0].message.content or "")
            out.append({"id": c["id"], "query": c["query"],
                        "faithfulness": 5 if scores["faithfulness"] else 1,
                        "relevancy": 5 if scores["relevancy"] else 1,
                        "correctness": 5 if scores["correctness"] else 1})
    finally:
        await provider.close()
    os.makedirs(os.path.dirname(LLM_SCORES), exist_ok=True)
    with open(LLM_SCORES, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"LLM-judge 打分已存 → {LLM_SCORES}")
    print("请人工按同一细则给这些用例打分，填进 logs/rag_judge_human_scores.json 后再跑 --mode calibrate。")


def run_calibrate() -> None:
    if not os.path.exists(LLM_SCORES) or not os.path.exists(HUMAN_SCORES):
        print("缺少 logs/rag_judge_llm_scores.json 或 logs/rag_judge_human_scores.json，先跑 --mode judge 并人工打分。")
        return
    llm = json.load(open(LLM_SCORES, encoding="utf-8"))
    human = json.load(open(HUMAN_SCORES, encoding="utf-8"))
    by_id = {h["id"]: h for h in human}
    aligned = [h for h in llm if h["id"] in by_id]
    print(f"对齐用例 {len(aligned)}/{len(llm)} 条")
    for metric in ("faithfulness", "relevancy", "correctness"):
        a = _as_binary(aligned, metric)
        b = _as_binary([by_id[h["id"]] for h in aligned], metric)
        k = cohens_kappa(a, b)
        agree = sum(1 for x, y in zip(a, b) if x == y) / len(a)
        verdict = "可信（可规模化）" if k >= 0.6 else "不可信（需换 judge 模型/细则，或加人工）"
        print(f"  [{metric}] kappa={k:.2f} 一致率={agree:.0%} → {verdict}")
    print("阈值建议：kappa≥0.6 可信；0.4-0.6 中等（慎用）；<0.4 不可信。")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["judge", "calibrate"], default="calibrate")
    a = ap.parse_args()
    if a.mode == "judge":
        asyncio.run(run_judge())
    else:
        run_calibrate()


if __name__ == "__main__":
    main()
