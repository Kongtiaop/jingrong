"""长时记忆接线：白名单过滤 + 跨会话活跃产品消解。"""
from types import SimpleNamespace

from app.services.customer_agent_execution import long_term_facts
from app.services.customer_intent import CustomerIntentPlanner

planner = CustomerIntentPlanner()
PRODUCT = "现金管理保本计划"


def _row(key, value):
    return SimpleNamespace(key=key, value=value)


def test_long_term_facts_whitelist_only():
    rows = [
        _row("active_product", PRODUCT),
        _row("risk_preference", "conservative"),
        _row("investment_horizon_preference", "3-5年"),
        _row("id_card", "110101199001010000"),   # 敏感/未白名单 → 丢弃
        _row("trade_history", "大量明细"),          # 未白名单 → 丢弃
        _row("active_product", ""),              # 空值 → 丢弃
    ]
    facts = long_term_facts(rows)
    assert facts.get("active_product") == PRODUCT
    assert facts.get("risk_preference") == "conservative"
    assert facts.get("investment_horizon_preference") == "3-5年"
    assert "id_card" not in facts
    assert "trade_history" not in facts


def test_cross_session_reference_resolves_from_long_term_facts():
    # 模拟"新会话"：短时记忆为空，只有长时记忆里有活跃产品
    short_term = {}
    facts = long_term_facts([_row("active_product", PRODUCT)])
    active_product = facts.get("active_product")
    plan = planner.plan("那个产品靠谱吗", context={"active_product": active_product})
    assert [t.intent for t in plan.tasks] == ["product_status"]
    assert plan.tasks[0].arguments["product_name"] == PRODUCT


def test_no_facts_keeps_safe_clarify():
    plan = planner.plan("那个产品靠谱吗", context={"active_product": None})
    assert [t.intent for t in plan.tasks] == ["unknown"]
    assert plan.clarification
