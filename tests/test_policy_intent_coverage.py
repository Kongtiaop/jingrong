# -*- coding: utf-8 -*-
"""policy 意图的触发覆盖（防止规则被改窄而无人察觉）。

ISSUE-20260914-POLICY-INTENT-NEVER-TRIGGERED

背景：`policy` 此前**只有一个触发词"政策"**，导致 31 条候选问句中 **0 条**能命中该意图：
```
"什么是双录"            -> ['unknown']
"什么是投资者适当性管理"   -> ['unknown']
"反洗钱相关规定有哪些"     -> ['unknown']
"什么是合格投资者"        -> ['unknown']
```
而 `policy` 类目下确有 **24 个客户可见 chunk**（《个人投资者适当性管理指南》等），
内容是适当性管理、投资者分类、风险匹配规则 —— 即**制度/规范**类。
用户不会问"什么是政策"，而是问上述**制度名词**，于是这批资料永远检索不到。

修复：按"该意图承载的内容"扩展触发词（适当性/双录/反洗钱/合格投资者/冷静期/
风险揭示/合规/管理办法/规定），而非按字面词"政策"。

为什么这类问题隐蔽：**意图从不触发**表现为"走到别的意图"或 `unknown`，
看上去像识别能力不足，实则是规则覆盖缺口；且不会报错、不会产生错误答案，
只是静默地少答。故用测试固定住典型触发面。
"""
import pytest

from app.services.customer_intent import CustomerIntentPlanner


@pytest.mark.parametrize("query", [
    "什么是双录",
    "什么是投资者适当性管理",
    "投资者适当性管理是什么",
    "反洗钱相关规定有哪些",
    "什么是合格投资者",
    "什么是冷静期",
    "合规要求有哪些",
    "适当性管理办法怎么规定",
])
def test_policy_intent_is_reachable(query):
    """制度类问题必须能命中 policy —— 否则那 24 个 chunk 永远检索不到。"""
    intents = [t.intent for t in CustomerIntentPlanner().plan(query).tasks]
    assert "policy" in intents, (
        f"{query!r} 未命中 policy（实际 {intents}）。"
        f"policy 类目下有适当性管理等制度资料，命中不了即用户问不到。")


def test_policy_trigger_expansion_does_not_steal_other_intents():
    """扩展触发词是"并存"而非"抢占"：原意图必须仍然命中。

    patterns 是收集式匹配（一个问句可命中多个意图，plan() 内按 seen 去重），
    故新增 policy 触发词不应让 risk_public / faq / workflow 等消失。
    """
    planner = CustomerIntentPlanner()
    keep = {
        "你们公司叫什么名字": "company_info",
        "你们公司的主营业务是什么": "company_business",
        "怎么联系你们": "faq",
        "开户流程是怎样的": "workflow",
        "风险等级怎么划分": "risk_public",
        "我的持仓": "asset_summary",
        "推荐适合我的产品": "product_recommendation",
        "我想转人工客服": "human_handoff",
    }
    for query, expected in keep.items():
        intents = [t.intent for t in planner.plan(query).tasks]
        assert expected in intents, \
            f"{query!r} 应仍命中 {expected}，实际 {intents}（policy 扩展抢占了它）"


def test_policy_terms_do_not_over_trigger_on_unrelated_queries():
    """普通问句不得因新增触发词而误带 policy。"""
    planner = CustomerIntentPlanner()
    for query in ("你好", "我的资产有多少", "推荐适合我的产品", "这个那个"):
        intents = [t.intent for t in planner.plan(query).tasks]
        assert "policy" not in intents, \
            f"{query!r} 不应命中 policy，实际 {intents}（触发词过宽）"
