import asyncio
import pytest

from app.services.llm_intent_fallback import FixtureIntentProvider, LLMIntentFallbackService
from app.services.customer_agent_execution import CustomerAgentExecutionService
from app.services.customer_intent import CustomerIntentPlanner
from app.services.intent_policy_resolver import IntentPolicyResolver
from app.schemas.intent import StructuredIntent


def _service() -> CustomerAgentExecutionService:
    svc = CustomerAgentExecutionService.__new__(CustomerAgentExecutionService)
    svc.settings = type("S", (), {
        "llm_intent_fallback_allowlist": "T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T7_HUMAN_HANDOFF"
    })()
    return svc


def _apply(query: str, *, template: str, primary_intent: str, confidence: float = 0.85):
    """走完整链路：规则 plan → 结构化 → resolver → 应用，返回 (规则意图, 应用后意图)。"""
    plan = CustomerIntentPlanner().plan(query)
    rule = CustomerAgentExecutionService._structured_from_rule_plan(plan)
    llm = StructuredIntent(template=template, primary_intent=primary_intent,
                           confidence=confidence, source="llm_fallback")
    resolved = IntentPolicyResolver().resolve(rule_intent=rule, llm_intent=llm)
    out = _service()._apply_structured_intent(plan, resolved)
    return (plan.tasks[0].intent if plan.tasks else "-"), (out.tasks[0].intent if out.tasks else "-")


# ---------- 回归：高置信事实类意图不得被 LLM 兜底降级 ----------
# 历史 bug：_structured_from_rule_plan 把 asset_summary 等压成 T1_INFORMATION_QUERY，
# 而 _apply_structured_intent 只看模板、用 primary_intent 重建 plan，
# 导致"查一下我的持仓"被降级成 faq —— 用户得到"资料不足，建议转人工"，
# 但真实持仓数据就在库里。线上 LLM_INTENT_FALLBACK_ENABLED=true，属于真实在发生的缺陷。

@pytest.mark.parametrize("query,expected", [
    ("查一下我的持仓", "asset_summary"),
    ("我的资产有多少", "asset_summary"),
    ("我的工单进展如何", "workorder_status"),
    ("我有没有风险限制", "risk_restriction"),
])
def test_high_confidence_business_fact_not_downgraded_to_faq(query, expected):
    before, after = _apply(query, template="T1_INFORMATION_QUERY", primary_intent="faq")
    assert before == expected
    assert after == expected, f"{query} 被 LLM 兜底降级成 {after}，事实类查询丢失真实数据能力"


def test_greeting_not_downgraded():
    """问候语 0.99 置信 → resolver 保护 → source=rule → 原样保留。"""
    before, after = _apply("你好", template="T1_INFORMATION_QUERY", primary_intent="faq")
    assert before == after == "greeting"


# ---------- 兜底能力必须保留（不能因为修 bug 而关掉模型兜底） ----------

def test_unknown_rule_still_benefits_from_llm_fallback():
    """规则识别不出（unknown）时，LLM 兜底仍应生效——这是兜底存在的意义。"""
    before, after = _apply("帮我看看有什么好货",
                           template="T2_PRODUCT_RECOMMENDATION", primary_intent="product_recommendation")
    assert before == "unknown"
    assert after == "product_recommendation"


def test_unknown_rule_can_fall_back_to_handoff():
    before, after = _apply("我想找个人聊聊", template="T7_HUMAN_HANDOFF", primary_intent="human_handoff")
    assert before == "unknown"
    assert after == "human_handoff"


# ---------- 安全红线：交易类模板永不来自模型 ----------

@pytest.mark.parametrize("template", ["T4_PURCHASE_DRAFT", "T3_RECOMMEND_THEN_PURCHASE_DRAFT"])
def test_trade_templates_never_applied_from_model(template):
    """交易类模板不在 allowlist，且 resolver 对 unknown→交易 强制澄清，双重拦截。"""
    before, after = _apply("随便说说", template=template, primary_intent="business_operation", confidence=0.99)
    assert before == after == "unknown"


def test_multi_knowledge_intent_not_collapsed_by_t1_fallback():
    """回归：T1 兜底不得把多个只读知识 task 收敛成单个。

    真实案例"你们公司的主营业务是什么"：planner 产出 company_info + company_business，
    管线 T1 分支重建 plan 只保留 primary_intent → task_intents 从 2 个变 1 个，
    用户少拿一路答案（图则保留 2 个，导致双跑对照报差异）。
    """
    plan = CustomerIntentPlanner().plan("你们公司的主营业务是什么")
    assert [t.intent for t in plan.tasks] == ["company_info", "company_business"]

    rule = CustomerAgentExecutionService._structured_from_rule_plan(plan)
    llm = StructuredIntent(template="T1_INFORMATION_QUERY", primary_intent="faq",
                           confidence=0.8, source="llm_fallback")
    resolved = IntentPolicyResolver().resolve(rule_intent=rule, llm_intent=llm)
    out = _service()._apply_structured_intent(plan, resolved)
    assert [t.intent for t in out.tasks] == ["company_info", "company_business"], \
        "多意图被 T1 兜底收敛成单 task"


def test_t1_fallback_still_applies_to_single_unknown_query():
    """单 task 的 unknown 查询仍可被 T1 兜底成 faq（保留模型兜底能力）。"""
    plan = CustomerIntentPlanner().plan("随便聊聊吧")
    rule = CustomerAgentExecutionService._structured_from_rule_plan(plan)
    llm = StructuredIntent(template="T1_INFORMATION_QUERY", primary_intent="faq",
                           confidence=0.8, source="llm_fallback")
    resolved = IntentPolicyResolver().resolve(rule_intent=rule, llm_intent=llm)
    out = _service()._apply_structured_intent(plan, resolved)
    assert [t.intent for t in out.tasks] == ["faq"]


def test_structured_from_rule_plan_preserves_original_intent():
    """primary_intent 必须保留规划器原始意图，而非模板桶——信息一旦丢失后续无法恢复。"""
    plan = CustomerIntentPlanner().plan("查一下我的持仓")
    structured = CustomerAgentExecutionService._structured_from_rule_plan(plan)
    assert structured.primary_intent == "asset_summary"
    assert structured.template == "T1_INFORMATION_QUERY"  # 模板仍是有损的，但不用于回写
    assert structured.source == "rule"


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
