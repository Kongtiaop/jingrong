import asyncio
import pytest

from app.services.llm_intent_fallback import FixtureIntentProvider, LLMIntentFallbackService


def test_fallback_validates_schema_and_forces_trade_confirmation_constraint():
    service = LLMIntentFallbackService(FixtureIntentProvider({
        "复杂表达": {
            "template": "T3_RECOMMEND_THEN_PURCHASE_DRAFT",
            "primary_intent": "recommend_then_purchase_draft",
            "secondary_intents": ["risk_preference_expression"],
            "entities": {"amount": {"value": 2000, "certainty": "approximate"}},
            "constraints": ["must_show_recommendations"],
            "missing_required_fields": ["selected_product"],
            "requires_clarification": True,
            "explicit_confirmation": True,
            "confidence": 0.85,
            "source": "llm_fallback",
        }
    }))
    result = asyncio.run(service.classify(query="复杂表达"))
    assert result.entities["amount"].value == 2000
    assert result.explicit_confirmation is False
    assert "must_not_submit_without_verified_customer_confirmation" in result.constraints


def test_invalid_provider_payload_is_rejected_by_schema():
    service = LLMIntentFallbackService(FixtureIntentProvider({"bad": {"template": "not-a-template"}}))
    with pytest.raises(Exception):
        asyncio.run(service.classify(query="bad"))
