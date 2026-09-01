from __future__ import annotations

from app.schemas.intent import StructuredEntity, StructuredIntent, StructuredIntentTask


INTENT_ALIASES = {
    "check_order_status": "order_status",
    "order_tracking": "order_status",
    "explain_risk_disclosure_in_plain_language": "risk_public",
    "risk_disclosure": "risk_public",
    "request_risk_explanation_in_plain_language": "risk_explanation",
    "information_query": "faq",
    "request_human_assistance": "human_handoff",
    "seek_human_assistance": "human_handoff",
    "recommendation": "product_recommendation",
    # 模型自由词（英文/大写变体）→ 规范意图码
    "trade_purchase": "business_operation",
    "trade_redeem": "business_operation",
    "purchase": "business_operation",
    "redeem": "business_operation",
    "trade": "business_operation",
    "buy": "business_operation",
    "sell": "business_operation",
    "purchase_draft": "business_operation",
    "trade_purchase_draft": "business_operation",
    "trade_operation": "business_operation",
    "risk_status": "risk_restriction",
    "check_restriction": "risk_restriction",
    "my_risk_level": "risk_public",
    "risk_level": "risk_public",
    "product_intro": "product_status",
    "product_detail": "product_status",
    "holdings": "asset_summary",
    "total_assets": "asset_summary",
    "asset": "asset_summary",
    "position": "asset_summary",
    "workorder": "workorder_status",
    # 中文口语/自由表达 → 规范意图码
    "查询订单状态": "order_status",
    "订单状态": "order_status",
    "查订单": "order_status",
    "订单": "order_status",
    "查询工单": "workorder_status",
    "工单状态": "workorder_status",
    "工单": "workorder_status",
    "查询产品信息": "product_status",
    "产品信息": "product_status",
    "产品": "product_status",
    "查询资产": "asset_summary",
    "持仓": "asset_summary",
    "资产": "asset_summary",
    "收益": "asset_summary",
    "查询风险": "risk_public",
    "风险": "risk_public",
    "产品推荐": "product_recommendation",
    "推荐": "product_recommendation",
    "申购": "business_operation",
    "购买": "business_operation",
    "下单": "business_operation",
    "转人工客服": "human_handoff",
    "转人工": "human_handoff",
    "投诉": "human_handoff",
    "咨询公司": "company_info",
    "公司业务": "company_business",
}

# 意图码 → 模板码（模型 template 不可靠时的派生）
INTENT_TO_TEMPLATE = {
    "order_status": "T5_ORDER_STATUS",
    "workorder_status": "T1_INFORMATION_QUERY",
    "asset_summary": "T1_INFORMATION_QUERY",
    "product_status": "T1_INFORMATION_QUERY",
    "faq": "T1_INFORMATION_QUERY",
    "policy": "T1_INFORMATION_QUERY",
    "company_info": "T1_INFORMATION_QUERY",
    "company_business": "T1_INFORMATION_QUERY",
    "risk_public": "T6_RISK_EXPLANATION",
    "risk_explanation": "T6_RISK_EXPLANATION",
    "risk_restriction": "T6_RISK_EXPLANATION",
    "product_recommendation": "T2_PRODUCT_RECOMMENDATION",
    "business_operation": "T4_PURCHASE_DRAFT",
    "human_handoff": "T7_HUMAN_HANDOFF",
    "unknown": "T0_UNKNOWN",
}

VALID_TEMPLATES = {
    "T1_INFORMATION_QUERY", "T2_PRODUCT_RECOMMENDATION", "T3_RECOMMEND_THEN_PURCHASE_DRAFT",
    "T4_PURCHASE_DRAFT", "T5_ORDER_STATUS", "T6_RISK_EXPLANATION", "T7_HUMAN_HANDOFF",
    "T8_MULTI_INTENT", "T9_MODIFY_DRAFT", "T10_CANCEL_DRAFT", "T0_UNKNOWN",
}


def canonical_template(intent: str | None, template: str | None) -> str:
    """模板容错：模型 template 不可靠时，按意图码派生或回退 T0。"""
    if template and template.strip() in VALID_TEMPLATES:
        return template.strip()
    return INTENT_TO_TEMPLATE.get(canonical_intent(intent), "T0_UNKNOWN")

# 中文关键词包含匹配（模型常返回长句中文意图，如"查询指定理财产品的详细信息"）
INTENT_KEYWORDS = [
    ("human_handoff", ["人工客服", "转人工", "投诉", "人工"]),
    ("order_status", ["订单状态", "订单", "成交"]),
    ("workorder_status", ["工单"]),
    ("asset_summary", ["持仓", "资产", "余额", "收益", "资金"]),
    ("business_operation", ["申购", "购买", "下单", "赎回", "买入", "转出", "退", "存"]),
    ("product_recommendation", ["推荐", "稳健产品"]),
    ("product_status", ["产品", "安全"]),
    # risk 歧义：更具体的先匹配；去掉笼统的"风险"，避免把产品安全/风险等级全混成 risk_public
    ("risk_restriction", ["风险限制", "被限制", "交易限制", "限制交易", "解除风险", "风控限制", "冻结"]),
    ("risk_explanation", ["解释风险", "风险解释", "什么是风险", "风险偏好", "风险是什么意思"]),
    ("risk_public", ["风险测评", "测评结果", "我的风险等级", "风险评估", "我是几级"]),
    ("policy", ["政策", "流程", "双录", "适当性", "冷静期", "条款", "期限", "手续费"]),
    ("company_business", ["公司业务", "公司服务", "业务范围"]),
    ("company_info", ["公司", "介绍", "联系方式"]),
]

CONSTRAINT_ALIASES = {
    "must_not_submit_before_confirmation": "must_not_submit_without_verified_customer_confirmation",
    "must_not_submit_without_confirmation": "must_not_submit_without_verified_customer_confirmation",
    "exclude_purchase_execution": "must_not_submit_without_verified_customer_confirmation",
    "must_not_auto_select": "must_not_auto_select_product",
}

TRADE_TEMPLATES = {"T3_RECOMMEND_THEN_PURCHASE_DRAFT", "T4_PURCHASE_DRAFT", "T9_MODIFY_DRAFT", "T10_CANCEL_DRAFT"}


def canonical_intent(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "unknown"
    if raw in INTENT_ALIASES:
        return INTENT_ALIASES[raw]
    for intent, keywords in INTENT_KEYWORDS:
        if any(kw in raw for kw in keywords):
            return intent
    # 合并理解层配置的额外关键词映射（模型可自调，运行时加载）
    for intent, keywords in _config_keywords():
        if any(kw in raw for kw in keywords):
            return intent
    return raw


def _config_keywords() -> list[tuple[str, list[str]]]:
    try:
        from app.services.understanding_config import understanding_config
        mappings = understanding_config.get_intent_mappings()
        out = []
        for intent, kws in (mappings.get("keywords") or {}).items():
            if isinstance(kws, list) and kws:
                out.append((intent, [str(k).lower() for k in kws]))
        return out
    except Exception:  # noqa: BLE001 - 配置缺失不影响
        return []


def canonical_constraint(value: str) -> str:
    return CONSTRAINT_ALIASES.get(value.strip().lower(), value.strip())


def normalize_constraints(values: list[str]) -> list[str]:
    return sorted({canonical_constraint(value) for value in values if value})


def _task_type(template: str, task_type: str) -> str:
    if template in TRADE_TEMPLATES:
        return "trade_workflow"
    if template == "T7_HUMAN_HANDOFF":
        return "human_handoff"
    if template == "T2_PRODUCT_RECOMMENDATION":
        return "recommendation"
    if template == "T0_UNKNOWN":
        return "unknown"
    return "read_only_query" if task_type not in {"recommendation", "trade_workflow", "human_handoff"} else task_type


class IntentNormalizer:
    """Converts model vocabulary into the application's stable intent contract."""

    def normalize(self, intent: StructuredIntent) -> StructuredIntent:
        tasks = [self._normalize_task(task) for task in intent.tasks]
        primary = canonical_intent(intent.primary_intent)
        secondary = sorted({canonical_intent(value) for value in intent.secondary_intents})
        template = intent.template
        constraints = normalize_constraints(intent.constraints)
        if template in TRADE_TEMPLATES:
            constraints = normalize_constraints(constraints + ["must_not_submit_without_verified_customer_confirmation"])
            intent = intent.model_copy(update={"explicit_confirmation": False})
        if tasks and len(tasks) > 1 and template not in TRADE_TEMPLATES:
            template = "T8_MULTI_INTENT"
            primary = canonical_intent(tasks[0].intent)
            secondary = sorted({canonical_intent(task.intent) for task in tasks[1:]})
        # requires_clarification = 缺关键字段 或 模型/任一 task 明确要澄清。
        # 安全确认不在此强制：交易确认由 always-added must_not_submit_without_verified_customer_confirmation
        # 约束在提交前独立保证，故"产品+金额齐全的申购"可不要求澄清（与规则路径语义对齐）。
        missing = list(intent.missing_required_fields or [])
        clarify = bool(intent.requires_clarification) or bool(missing) or any(t.requires_clarification for t in tasks)
        return intent.model_copy(update={
            "template": template,
            "primary_intent": primary,
            "secondary_intents": secondary,
            "constraints": constraints,
            "tasks": tasks,
            "requires_clarification": clarify,
        })

    def _normalize_task(self, task: StructuredIntentTask) -> StructuredIntentTask:
        template = task.template
        constraints = normalize_constraints(task.constraints)
        if template in TRADE_TEMPLATES:
            constraints = normalize_constraints(constraints + ["must_not_submit_without_verified_customer_confirmation", "must_not_auto_select_product"])
        return task.model_copy(update={
            "intent": canonical_intent(task.intent),
            "task_type": _task_type(template, task.task_type),
            "constraints": constraints,
            # 不强制交易 task 恒澄清：缺字段由 normalize 层合并 missing_required_fields 判定。
            "requires_clarification": task.requires_clarification,
        })
