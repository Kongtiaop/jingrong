import json
from pathlib import Path

import pytest

from app.services.customer_intent import CustomerIntentPlanner
from app.services.intent_template_resolver import IntentTemplateResolver


CASES = json.loads((Path(__file__).parent / "fixtures" / "complex_customer_intent_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_complex_customer_intent_rule_baseline(case):
    plan = CustomerIntentPlanner().plan(case["query"])
    result = IntentTemplateResolver().resolve(case["query"], plan)

    assert result.template == case["expected_template"]
    assert result.primary_intent == case["expected_primary"]
    expected_amount = case.get("expected_amount")
    actual_amount = result.entities.get("amount")
    if expected_amount is None:
        assert actual_amount is None or actual_amount.value is None
    else:
        assert actual_amount is not None
        assert actual_amount.value == expected_amount
    for constraint in case.get("required_constraints", []):
        assert constraint in result.constraints
    assert result.requires_clarification is case["requires_clarification"]


def test_fixture_covers_required_human_language_categories():
    categories = {case["category"] for case in CASES}
    assert {
        "recommend_then_purchase", "purchase_ordering", "purchase_missing",
        "negative_condition", "multi_intent", "ambiguous_context", "adversarial",
    }.issubset(categories)
