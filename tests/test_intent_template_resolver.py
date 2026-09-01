from app.services.customer_intent import CustomerIntentPlanner
from app.services.intent_template_resolver import IntentTemplateResolver


def resolve(query: str):
    return IntentTemplateResolver().resolve(query, CustomerIntentPlanner().plan(query))


def test_recommend_then_purchase_keeps_amount_and_requires_product_selection():
    result = resolve("推荐一个产品并申购1000元")
    assert result.template == "T3_RECOMMEND_THEN_PURCHASE_DRAFT"
    assert result.entities["amount"].value == 1000
    assert "selected_product" in result.missing_required_fields
    assert "must_not_auto_select_product" in result.constraints


def test_negative_execution_constraint_is_preserved():
    result = resolve("给我找个稳一点的产品，合适就准备1000元，但不要直接买")
    assert "must_not_submit_without_verified_customer_confirmation" in result.constraints


def test_order_status_maps_to_read_only_template():
    result = resolve("帮我看下订单状态")
    assert result.template == "T5_ORDER_STATUS"
