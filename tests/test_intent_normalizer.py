from app.schemas.intent import StructuredIntent, StructuredIntentTask
from app.services.intent_normalizer import IntentNormalizer


def test_normalizer_maps_free_form_readonly_aliases_and_multi_task_template():
    result = IntentNormalizer().normalize(StructuredIntent(
        template="T1_INFORMATION_QUERY",
        primary_intent="check_order_status",
        secondary_intents=["explain_risk_disclosure_in_plain_language"],
        confidence=0.8,
        source="llm_fallback",
        tasks=[
            StructuredIntentTask(intent="check_order_status", template="T5_ORDER_STATUS", task_type="read_only_query", confidence=0.8),
            StructuredIntentTask(intent="risk_disclosure", template="T1_INFORMATION_QUERY", task_type="read_only_query", confidence=0.8),
        ],
    ))
    assert result.template == "T8_MULTI_INTENT"
    assert result.primary_intent == "order_status"
    assert "risk_public" in result.secondary_intents
    assert result.tasks[0].intent == "order_status"
    assert result.tasks[1].intent == "risk_public"


def test_normalizer_forces_trade_task_type_and_guards():
    result = IntentNormalizer().normalize(StructuredIntent(
        template="T4_PURCHASE_DRAFT",
        primary_intent="purchase_draft",
        confidence=0.9,
        explicit_confirmation=True,
        source="llm_fallback",
        tasks=[StructuredIntentTask(intent="purchase_draft", template="T4_PURCHASE_DRAFT", task_type="read_only_query", confidence=0.9)],
    ))
    assert result.tasks[0].task_type == "trade_workflow"
    assert result.explicit_confirmation is False
    assert "must_not_submit_without_verified_customer_confirmation" in result.constraints
    assert "must_not_auto_select_product" in result.tasks[0].constraints
