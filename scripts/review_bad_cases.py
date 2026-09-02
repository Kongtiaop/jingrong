#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审查线上 bad case 积累池（`logs/bad_cases.jsonl`）。

用法：
  列出全部 bad case：            python -m scripts.review_bad_cases
  只看某类原因：                  python -m scripts.review_bad_cases --reason intent_unknown
  导出为待标注清单（喂给人工/后续升级评测集）：
    python -m scripts.review_bad_cases --export logs/bad_cases_review.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os

from app.services.bad_case_collector import BadCaseStore, DEFAULT_STORE

REASON_LABEL = {
    "intent_unknown": "意图未识别(unknown)",
    "intent_low_confidence": "意图低置信(<0.5)",
    "intent_rules_fallback": "LLM解析失败→规则兜底",
    "intent_misroute": "高置信误判(规则vs影子分歧)",
    "rag_low_confidence": "RAG证据不足",
    "rag_embedding_degraded": "RAG向量降级",
    "rag_text_fallback": "RAG纯文本兜底",
    "rag_handoff": "RAG转人工",
    "clarify_stuck": "澄清>=2次未问清",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--reason", default=None, help="按原因过滤")
    ap.add_argument("--export", default=None, help="导出 JSON 到指定路径")
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()

    store = BadCaseStore(a.store)
    rows = store.load()
    if not rows:
        print(f"积累池为空：{a.store}（尚无线上 bad case）")
        return

    reasons = collections.Counter()
    for r in rows:
        for reason in r.get("reasons", []):
            reasons[reason] += 1
    print(f"积累池共 {len(rows)} 条（{a.store}）")
    print("按原因分布：")
    for reason, cnt in reasons.most_common():
        print(f"  {REASON_LABEL.get(reason, reason)}: {cnt}")

    filtered = [r for r in rows if a.reason is None or a.reason in r.get("reasons", [])]
    print(f"\n明细（过滤后 {len(filtered)} 条，最多显示 {a.limit}）:")
    for r in filtered[:a.limit]:
        print(f"  [{','.join(r.get('reasons', []))}] {r.get('query')} | intent={r.get('intent')} conf={r.get('confidence')} ev={r.get('evidence_status')} ts={r.get('ts')}")

    if a.export:
        os.makedirs(os.path.dirname(a.export) or ".", exist_ok=True)
        with open(a.export, "w", encoding="utf-8") as f:
            json.dump({"count": len(filtered), "reason": a.reason, "cases": filtered}, f, ensure_ascii=False, indent=2)
        print(f"\n已导出 {len(filtered)} 条 → {a.export}（供人工标注期望意图后升级进评测集）")


if __name__ == "__main__":
    main()
