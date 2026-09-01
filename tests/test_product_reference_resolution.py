"""产品指代消解：当前句用指代词指向上下文中的产品时，应解析成 product_status。"""
from app.services.customer_intent import CustomerIntentPlanner

planner = CustomerIntentPlanner()
PRODUCT = "现金管理保本计划"


def _intent(plan):
    return [t.intent for t in plan.tasks]


def test_reference_resolves_to_product_status_when_context_has_active_product():
    plan = planner.plan("帮我看看这个理财靠不靠谱", context={"active_product": PRODUCT})
    assert _intent(plan) == ["product_status"]
    task = plan.tasks[0]
    assert task.arguments["product_name"] == PRODUCT
    # 产品名必须拼进 query，_product_status 按 query 文本匹配产品
    assert PRODUCT in task.query
    assert plan.clarification is None  # 已消解，无需澄清


def test_reference_stays_unknown_without_context():
    plan = planner.plan("帮我看看这个理财靠不靠谱")
    assert _intent(plan) == ["unknown"]
    assert plan.clarification  # 安全兜底：请用户澄清


def test_temporal_ref_not_resolved_to_product():
    # “这个月”是时间，不是产品指代
    plan = planner.plan("这个月收益怎么样", context={"active_product": PRODUCT})
    assert _intent(plan) != ["product_status"]


def test_marker_still_wins_over_reference():
    # 有明确 marker（订单）时不因 active_product 误判成产品
    plan = planner.plan("这个订单到哪一步了", context={"active_product": PRODUCT})
    assert _intent(plan) == ["order_status"]


def test_bare_active_product_keeps_arguments():
    plan = planner.plan("它", context={"active_product": PRODUCT})
    if plan.tasks and plan.tasks[0].intent == "product_status":
        assert plan.tasks[0].arguments["product_name"] == PRODUCT


# ---- 裸产品名句识别 ----
def test_bare_product_name_routes_to_product_status():
    for q in ("现金管理保本计划靠谱吗", "平衡配置组合收益如何", "稳健增值计划怎么样"):
        plan = planner.plan(q)
        assert _intent(plan) == ["product_status"], q


def test_bare_product_name_keeps_query_text():
    # 产品名必须留在 query 文本里，_product_status 靠子串匹配
    plan = planner.plan("现金管理保本计划靠谱吗")
    assert PRODUCT in plan.tasks[0].query


def test_temporal_and_personal_not_forced_to_product():
    assert _intent(planner.plan("这个月收益怎么样")) != ["product_status"]
    assert _intent(planner.plan("我的收益怎么样")) != ["product_status"]


def test_reference_pronoun_not_forced_to_product():
    # 指代句（它安全吗）在无上下文时应走澄清，不硬路由成产品
    assert _intent(planner.plan("它安全吗")) != ["product_status"]
