from __future__ import annotations

import json
import importlib.util
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "app" / "services" / "nl2sql_policy.py"
spec = importlib.util.spec_from_file_location("nl2sql_policy", POLICY_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load nl2sql_policy")
nl2sql_policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nl2sql_policy)


CASES_PATH = Path(__file__).resolve().parents[1] / "tests" / "nl2sql_eval_cases.json"


def classify(case: dict) -> dict:
    started = time.perf_counter()
    safe, reason = nl2sql_policy.evaluate_query_safety(case["question"])
    if not safe:
        actual_outcome = "refusal"
        actual_metric = None
    else:
        actual_metric = nl2sql_policy.metric_code_for_query(case["question"])
        actual_outcome = "answer" if actual_metric else "clarification"
    permission_allowed = case.get("permission_allowed", True)
    catalog_scope_allowed = case.get("catalog_scope_allowed", True)
    template_approved = case.get("template_approved", True)
    if actual_outcome == "answer" and not permission_allowed:
        actual_outcome, actual_metric, reason = "refusal", None, "permission_denied"
    elif actual_outcome == "answer" and not catalog_scope_allowed:
        actual_outcome, actual_metric, reason = "refusal", None, "catalog_table_scope_denied"
    elif actual_outcome == "answer" and not template_approved:
        actual_outcome, actual_metric, reason = "refusal", None, "template_not_approved"
    elapsed_ms = (time.perf_counter() - started) * 1000
    expected_outcome = case["expected_outcome"]
    syntax_ok = actual_outcome != "refusal" or case["should_refuse"]
    semantic_ok = actual_outcome == expected_outcome and (actual_outcome != "answer" or actual_metric == case["expected_metric_or_query"])
    security_incident = actual_outcome != "refusal" and bool(case["security_tags"])
    return {
        "case_id": case["case_id"],
        "expected_outcome": expected_outcome,
        "actual_outcome": actual_outcome,
        "expected_metric_or_query": case["expected_metric_or_query"],
        "actual_metric_or_query": actual_metric,
        "syntax_ok": syntax_ok,
        "semantic_ok": semantic_ok,
        "refusal_expected": case["should_refuse"],
        "refusal_actual": actual_outcome == "refusal",
        "clarification_expected": case["clarification_required"],
        "clarification_actual": actual_outcome == "clarification",
        "security_incident": security_incident,
        "latency_ms": elapsed_ms,
        "safety_reason": reason or None,
    }


def percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * 0.95)))
    return ordered[index]


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    results = [classify(case) for case in cases]
    total = len(results)
    report = {
        "total_cases": total,
        "syntax_correct_rate": sum(item["syntax_ok"] for item in results) / total,
        "semantic_accuracy": sum(item["semantic_ok"] for item in results) / total,
        "refusal_rate": sum(item["refusal_actual"] for item in results) / total,
        "clarification_trigger_rate": sum(item["clarification_actual"] for item in results) / total,
        "p95_latency_ms": percentile_95([item["latency_ms"] for item in results]),
        "security_incident_count": sum(item["security_incident"] for item in results),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["security_incident_count"]:
        raise SystemExit("security incidents detected")


if __name__ == "__main__":
    main()
