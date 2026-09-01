from __future__ import annotations

import re

from app.schemas.intent import StructuredEntity, StructuredIntent


class IntentTemplateResolver:
    """Rule baseline for mapping plans to standard task templates.

    Future LLM fallback must return the same StructuredIntent contract.
    """

    def resolve(self, query: str, plan) -> StructuredIntent:
        intents = [task.intent for task in plan.tasks]
        normalized = re.sub(r"\s+", "", query)
        has_recommendation = "product_recommendation" in intents or any(marker in normalized for marker in ("适合什么产品", "推荐一下", "推荐一个", "找个稳", "先给我推荐"))
        has_purchase = ("business_operation" in intents or any(marker in normalized for marker in ("申购", "购买", "买入", "下单", "想买", "直接给我买"))) and any(marker in normalized for marker in ("申购", "购买", "买入", "下单", "想买", "直接给我买", "投", "准备好"))
        amount_match = re.search(r"([0-9]+(?:\.[0-9]+)?)(万|元)", normalized)
        amount = None
        certainty = "missing"
        if amount_match:
            raw = float(amount_match.group(1))
            amount = raw * 10000 if amount_match.group(2) == "万" else raw
            certainty = "explicit"
        else:
            chinese_amount = re.search(r"(一千|两千|二千|三千|四千|五千|一万|两万|二万|一百|两百|二百)", normalized)
            if chinese_amount:
                amount = {"一千": 1000, "两千": 2000, "二千": 2000, "三千": 3000, "四千": 4000, "五千": 5000, "一万": 10000, "两万": 20000, "二万": 20000, "一百": 100, "两百": 200, "二百": 200}[chinese_amount.group(1)]
                certainty = "approximate"
            else:
                standalone_amount = re.search(r"(?:投|放|准备)([0-9]+(?:\.[0-9]+)?)", normalized)
                if standalone_amount:
                    amount = float(standalone_amount.group(1))
                    certainty = "explicit"
        constraints: list[str] = []
        if any(marker in normalized for marker in ("不要直接买", "先别买", "别直接买", "不要下单", "最后必须让我确认", "直接给我买")):
            constraints.append("must_not_submit_without_verified_customer_confirmation")
        # Complex natural language can express a recommendation-to-draft request
        # without literal “推荐”/“申购” keywords; this conservative heuristic only
        # classifies it as a template and never authorizes automatic execution.
        inferred_recommend_then_purchase = (
            amount is not None
            and any(marker in normalized for marker in ("合适", "稳一点", "稳健", "低波动", "帮我找", "找个"))
            and any(marker in normalized for marker in ("准备", "买", "投", "放进去", "准备好"))
        )
        if (has_recommendation and has_purchase) or inferred_recommend_then_purchase:
            return StructuredIntent(
                template="T3_RECOMMEND_THEN_PURCHASE_DRAFT", primary_intent="recommend_then_purchase_draft",
                secondary_intents=["product_recommendation", "business_operation"],
                entities={"amount": StructuredEntity(value=amount, certainty=certainty)},
                constraints=constraints + ["must_show_recommendations", "must_not_auto_select_product"],
                missing_required_fields=["selected_product"] + ([] if amount is not None else ["amount"]),
                requires_clarification=True, explicit_confirmation=False, confidence=0.95, source="rule",
            )
        if len(intents) > 1 and "order_status" in intents:
            return StructuredIntent(template="T8_MULTI_INTENT", primary_intent=intents[0], secondary_intents=intents[1:], confidence=0.75, source="rule")
        if has_recommendation:
            return StructuredIntent(template="T2_PRODUCT_RECOMMENDATION", primary_intent="product_recommendation", confidence=0.92, source="rule")
        if has_purchase:
            missing = [] if amount is not None else ["amount"]
            trade_constraints = ["must_not_submit_without_verified_customer_confirmation"] if any(marker in normalized for marker in ("直接给我买", "不管适不适合", "不要确认")) else []
            return StructuredIntent(template="T4_PURCHASE_DRAFT", primary_intent="purchase_draft", entities={"amount": StructuredEntity(value=amount, certainty=certainty)}, constraints=trade_constraints, missing_required_fields=missing, requires_clarification=bool(missing), confidence=0.9, source="rule")
        if "human_handoff" in intents:
            return StructuredIntent(template="T7_HUMAN_HANDOFF", primary_intent="human_handoff", confidence=0.99, source="rule")
        if "order_status" in intents:
            return StructuredIntent(template="T5_ORDER_STATUS", primary_intent="order_status", confidence=0.92, source="rule")
        if "risk_restriction" in intents:
            return StructuredIntent(template="T6_RISK_EXPLANATION", primary_intent="risk_explanation", confidence=0.92, source="rule")
        if len(intents) > 1:
            return StructuredIntent(template="T8_MULTI_INTENT", primary_intent=intents[0], secondary_intents=intents[1:], confidence=0.75, source="rule")
        return StructuredIntent(template="T0_UNKNOWN", primary_intent="unknown", requires_clarification=True, confidence=0.2, source="rule")
