from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import get_settings
from app.infrastructure.qwen import QwenProvider
from app.services.customer_intent import CustomerIntentPlanner
from app.services.intent_policy_resolver import IntentPolicyResolver
from app.services.intent_template_resolver import IntentTemplateResolver
from app.services.llm_intent_fallback import LLMIntentFallbackService


def rule_intent(query: str):
    plan = CustomerIntentPlanner().plan(query)
    return IntentTemplateResolver().resolve(query, plan)


def evaluate_case(case, result):
    failures = []
    if result.template != case["expected_template"]:
        failures.append(f"template:{result.template}")
    # Template is the executable routing contract; model-owned free-form labels are diagnostic only.
    if result.source != "llm_fallback" and result.primary_intent != case["expected_primary"]:
        failures.append(f"primary:{result.primary_intent}")
    amount = result.entities.get("amount")
    actual_amount = amount.value if amount else None
    expected_amount = case.get("expected_amount")
    if expected_amount is not None and actual_amount != expected_amount:
        failures.append(f"amount:{actual_amount}")
    for constraint in case.get("required_constraints", []):
        if constraint not in result.constraints:
            failures.append(f"constraint:{constraint}")
    if result.template in {"T3_RECOMMEND_THEN_PURCHASE_DRAFT", "T4_PURCHASE_DRAFT"} and result.explicit_confirmation:
        failures.append("unsafe_confirmation=true")
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default="tests/fixtures/complex_customer_intent_cases.json")
    parser.add_argument("--live", action="store_true", help="Call Qwen; requires DASHSCOPE_API_KEY")
    parser.add_argument("--output", default="logs/intent_fallback_evaluation.json")
    args = parser.parse_args()
    cases = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    baseline = []
    for case in cases:
        result = rule_intent(case["query"])
        baseline.append({"id": case["id"], "failures": evaluate_case(case, result), "template": result.template, "primary_intent": result.primary_intent})
    report = {"fixture": args.fixture, "case_count": len(cases), "baseline_failures": sum(bool(row["failures"]) for row in baseline), "baseline": baseline, "live": False}
    if args.live:
        settings = get_settings()
        provider = QwenProvider(settings)
        try:
            fallback = LLMIntentFallbackService(provider)
            live_rows = []
            for case in cases:
                rule = rule_intent(case["query"])
                try:
                    candidate = await fallback.classify(query=case["query"])
                    resolved = IntentPolicyResolver().resolve(rule_intent=rule, llm_intent=candidate)
                    live_rows.append({"id": case["id"], "failures": evaluate_case(case, resolved), "template": resolved.template, "primary_intent": resolved.primary_intent, "source": resolved.source})
                except Exception as exc:
                    live_rows.append({"id": case["id"], "error": f"{type(exc).__name__}: {exc}"})
            report["live"] = True
            report["live_results"] = live_rows
            report["live_failures"] = sum(bool(row.get("failures") or row.get("error")) for row in live_rows)
        finally:
            await provider.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in {"baseline", "live_results"}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
