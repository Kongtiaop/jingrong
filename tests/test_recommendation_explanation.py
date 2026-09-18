# -*- coding: utf-8 -*-
"""推荐依据追问：问「为什么」应给依据，而不是再给一份清单。

背景（2026-09-18 实测）：`_patterns` 里 ("product_recommendation", "推荐", ..., .88)
只要句中出现"推荐"二字就把整句判成产品推荐，疑问词完全不起作用。
于是用户问「为什么推荐这些产品给我」，拿到的回答与「给我推荐」一字不差。
"""
from types import SimpleNamespace

from app.services.customer_intent import CustomerIntentPlanner, execution_mode_for_intent
from app.services.customer_task_executor import explain_recommendation


def _first_intent(query: str) -> str:
    plan = CustomerIntentPlanner().plan(query)
    return plan.tasks[0].intent if plan.tasks else ""


# ---------- 意图识别 ----------

def test_why_recommend_goes_to_explanation():
    for query in (
        "为什么推荐这些产品给我",
        "为什么推荐这个",
        "推荐的理由是什么",
        "凭什么推荐这些产品",
        "这些产品是怎么选出来的",
    ):
        assert _first_intent(query) == "recommendation_explanation", query


def test_plain_recommend_still_goes_to_recommendation():
    """新增规则不能误伤原本正常的推荐请求。"""
    for query in (
        "推荐适合我的产品",
        "给我推荐产品",
        "帮我推荐几只产品",
        "我能买什么产品",
    ):
        assert _first_intent(query) == "product_recommendation", query


def test_explanation_uses_deterministic_execution_mode():
    """解释基于确定性的适当性数据，不应走 bounded_react（那是给知识检索的）。"""
    assert execution_mode_for_intent("recommendation_explanation") == "controlled_workflow"


# ---------- 解释文本 ----------

def _rec(**overrides):
    base = dict(
        context=SimpleNamespace(risk_level="C1", risk_assessment_status="provisional",
                                total_asset=302543.82, investable_asset=302543.82),
        matches=[
            SimpleNamespace(product_name="中短债增利计划", product_risk_level="C2",
                            minimum_amount=10000.0, recommendation_reason="满足 C2 适当性要求",
                            reasons=["风险等级匹配"]),
            SimpleNamespace(product_name="同业存单指数基金", product_risk_level="C1",
                            minimum_amount=5000.0, recommendation_reason="",
                            reasons=["风险等级匹配", "起购金额在可投资产内"]),
        ],
        excluded=[SimpleNamespace(product_name="权益增强组合", product_risk_level="C5")],
        warnings=[SimpleNamespace(code="provisional", message="风险测评尚未完成，结果可能调整")],
        # 贴近 suitability_service.py:172 的真实形态——它本身就是产品清单的浓缩
        preference_note="以下 2 个产品通过适当性匹配：中短债增利计划（满足 C2 适当性要求）",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_explanation_states_the_actual_rule():
    """必须说清判定规则——这是用户问「为什么」时真正想要的。"""
    text = explain_recommendation(_rec())
    assert "C1" in text, "应给出用户自己的风险等级"
    assert "适当性" in text
    assert "不高于" in text and "起购" in text, "应说明两条硬性筛选条件"


def test_explanation_lists_matches_with_reasons():
    text = explain_recommendation(_rec())
    assert "中短债增利计划" in text
    assert "满足 C2 适当性要求" in text
    # recommendation_reason 为空时回退用 reasons 拼接
    assert "同业存单指数基金" in text and "起购金额在可投资产内" in text
    assert "10,000.00" in text, "应带上起购门槛，便于用户自行核对"


def test_explanation_mentions_excluded_and_warnings():
    text = explain_recommendation(_rec())
    assert "未通过匹配" in text and "1 个" in text
    assert "风险测评尚未完成" in text


def test_explanation_does_not_repeat_preference_note():
    """preference_note 本身就是产品清单的浓缩，解释里不能再拼一遍。

    实测出现过复读：正文逐条列完产品，末尾又追加
    「以下 2 个产品通过适当性匹配：…」。
    """
    text = explain_recommendation(_rec())
    assert "以下 2 个产品通过适当性匹配" not in text, "不得重复 preference_note"


def test_explanation_handles_empty_matches():
    """一个都没匹配上时不能抛异常，也要给出可解释的结论。"""
    text = explain_recommendation(_rec(matches=[], excluded=[], warnings=[], preference_note=""))
    assert "没有产品通过匹配" in text


def test_explanation_survives_missing_optional_fields():
    """字段缺失（老数据/接口变更）时应降级而非崩——解释是读侧，不该因此 500。"""
    text = explain_recommendation(SimpleNamespace(context=SimpleNamespace(), matches=[], excluded=[]))
    assert "未测评" in text
