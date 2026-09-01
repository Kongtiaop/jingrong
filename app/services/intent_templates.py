from __future__ import annotations

from app.schemas.intent import IntentTemplate


TEMPLATES: dict[str, IntentTemplate] = {
    "T1_INFORMATION_QUERY": IntentTemplate(code="T1_INFORMATION_QUERY", description="客户只读事实或知识查询", execution_mode="read_only"),
    "T2_PRODUCT_RECOMMENDATION": IntentTemplate(code="T2_PRODUCT_RECOMMENDATION", description="基于适当性规则展示推荐", execution_mode="plan_rules", prohibited_actions=["auto_select_product", "submit_order"]),
    "T3_RECOMMEND_THEN_PURCHASE_DRAFT": IntentTemplate(code="T3_RECOMMEND_THEN_PURCHASE_DRAFT", description="先推荐，再由客户选择产品并生成申购草稿", execution_mode="controlled_workflow", required_fields=["selected_product", "amount"], prohibited_actions=["auto_select_product", "submit_order_without_confirmation"]),
    "T4_PURCHASE_DRAFT": IntentTemplate(code="T4_PURCHASE_DRAFT", description="明确申购草稿", execution_mode="controlled_workflow", required_fields=["product_name", "amount"], prohibited_actions=["submit_order_without_confirmation"]),
    "T5_ORDER_STATUS": IntentTemplate(code="T5_ORDER_STATUS", description="订单状态查询和解释", execution_mode="read_only"),
    "T6_RISK_EXPLANATION": IntentTemplate(code="T6_RISK_EXPLANATION", description="客户自身风险限制解释", execution_mode="read_only"),
    "T7_HUMAN_HANDOFF": IntentTemplate(code="T7_HUMAN_HANDOFF", description="人工客服交接", execution_mode="controlled_workflow"),
    "T8_MULTI_INTENT": IntentTemplate(code="T8_MULTI_INTENT", description="多任务查询和建议", execution_mode="plan_rules"),
    "T9_MODIFY_DRAFT": IntentTemplate(code="T9_MODIFY_DRAFT", description="修改草稿参数，要求重新预检", execution_mode="controlled_workflow", prohibited_actions=["reuse_old_confirmation"]),
    "T10_CANCEL_DRAFT": IntentTemplate(code="T10_CANCEL_DRAFT", description="取消草稿", execution_mode="controlled_workflow"),
    "T0_UNKNOWN": IntentTemplate(code="T0_UNKNOWN", description="无法安全分类", execution_mode="read_only"),
}


def get_template(code: str) -> IntentTemplate:
    return TEMPLATES.get(code, TEMPLATES["T0_UNKNOWN"])
