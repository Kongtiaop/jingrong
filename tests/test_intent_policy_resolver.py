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


def test_policy_allows_unknown_to_lift_to_non_trade_recommendation():
    # 放宽非交易兜底：unknown 规则可由高置信 LLM 提升为 推荐（T2）—— 不执行任何动作，安全。
    rule = StructuredIntent(template="T0_UNKNOWN", primary_intent="unknown", confidence=0.2, source="rule", requires_clarification=True)
    llm = StructuredIntent(template="T2_PRODUCT_RECOMMENDATION", primary_intent="product_recommendation", confidence=0.8, source="llm_fallback", explicit_confirmation=True)
    result = IntentPolicyResolver().resolve(rule_intent=rule, llm_intent=llm)
    assert result.template == "T2_PRODUCT_RECOMMENDATION"
    assert result.primary_intent == "product_recommendation"
    # 仍绝不允许在缺失上下文时由模型直接落成一笔交易：确认必须保持关闭。
    assert result.explicit_confirmation is False
