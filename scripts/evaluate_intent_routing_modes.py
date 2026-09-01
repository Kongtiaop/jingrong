from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.intent import StructuredIntent
from app.services.customer_agent_execution import CustomerAgentExecutionService
from app.services.intent_policy_resolver import IntentPolicyResolver
from app.services.intent_template_resolver import IntentTemplateResolver
from app.services.customer_intent import CustomerIntentPlanner
from app.services.llm_intent_fallback import LLMIntentFallbackService


class FixtureProvider:
    def __init__(self, fixtures: dict[str, dict]):
        self.fixtures = fixtures

    async def classify(self, *, query: str, conversation_summary: str = "") -> dict:
        return self.fixtures[query]


def service_for(*, fallback_enabled: bool) -> CustomerAgentExecutionService:
    settings = SimpleNamespace(
        llm_intent_shadow_enabled=False,
        llm_intent_fallback_enabled=fallback_enabled,
        llm_intent_fallback_timeout_seconds=2,
        llm_intent_fallback_allowlist="T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T7_HUMAN_HANDOFF",
        llm_intent_fallback_max_concurrency=2,
    )
    return CustomerAgentExecutionService(SimpleNamespace(session_factory=lambda: None), None, settings=settings)


async def main() -> None:
    fixture = json.loads(Path("tests/fixtures/complex_customer_intent_cases.json").read_text(encoding="utf-8"))
    cases = {item["query"]: item for item in fixture}
    model_payloads = {
        "你看着办吧": {
            "template": "T2_PRODUCT_RECOMMENDATION", "primary_intent": "product_recommendation",
            "confidence": 0.91, "requires_clarification": False, "entities": {},
            "constraints": [], "source": "llm_fallback",
        },
    }
    fallback = LLMIntentFallbackService(FixtureProvider(model_payloads))
    rule = CustomerIntentPlanner().plan("你看着办吧")
    rule_structured = IntentTemplateResolver().resolve("你看着办吧", rule)
    model = await fallback.classify(query="你看着办吧")
    resolved = IntentPolicyResolver().resolve(rule_intent=rule_structured, llm_intent=model)
    contained = service_for(fallback_enabled=True)._apply_structured_intent(rule, resolved)
    report = {
        "case": "你看着办吧",
        "rule_template": rule_structured.template,
        "model_template": model.template,
        "resolved_template": resolved.template,
        "execution_changed": contained != rule,
        "safe": resolved.template == "T0_UNKNOWN" and resolved.requires_clarification,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["safe"] or report["execution_changed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
