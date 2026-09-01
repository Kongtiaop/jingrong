from app.schemas.intent import StructuredEntity, StructuredIntent
from app.services.intent_policy_resolver import IntentPolicyResolver


def test_policy_preserves_explicit_rule_amount_and_confirmation_guard():
    rule = StructuredIntent(
        template="T4_PURCHASE_DRAFT", primary_intent="purchase_draft",
        entities={"amount": StructuredEntity(value=1000, certainty="explicit")},
        constraints=["must_not_submit_without_verified_customer_confirmation"], confidence=0.95, source="rule",
    )
    llm = StructuredIntent(
        template="T3_RECOMMEND_THEN_PURCHASE_DRAFT", primary_intent="recommend_then_purchase_draft",
        entities={"amount": StructuredEntity(value=2000, certainty="inferred")},
        confidence=0.7, source="llm_fallback", explicit_confirmation=True,
    )
    result = IntentPolicyResolver().resolve(rule_intent=rule, llm_intent=llm)
    assert result.template == "T4_PURCHASE_DRAFT"
    assert result.entities["amount"].value == 1000
    assert "must_not_submit_without_verified_customer_confirmation" in result.constraints


def test_policy_does_not_upgrade_unknown_context_to_trade_workflow():
    rule = StructuredIntent(template="T0_UNKNOWN", primary_intent="unknown", confidence=0.2, source="rule", requires_clarification=True)
    llm = StructuredIntent(template="T3_RECOMMEND_THEN_PURCHASE_DRAFT", primary_intent="recommend_then_purchase_draft", confidence=0.8, source="llm_fallback", explicit_confirmation=True)
    result = IntentPolicyResolver().resolve(rule_intent=rule, llm_intent=llm)
    assert result.template == "T0_UNKNOWN"
    assert result.requires_clarification is True
    assert result.explicit_confirmation is False
    assert "must_not_submit_without_verified_customer_confirmation" in result.constraints
