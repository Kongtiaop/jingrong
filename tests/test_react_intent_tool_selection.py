# -*- coding: utf-8 -*-
"""确定性 ReAct 回退必须按意图选工具 + 失败文案按工具类型区分。

ISSUE-20260914-REACT-FALLBACK-IGNORES-INTENT：
  `react_step_node` 曾硬编码 `search_customer_knowledge`，对**事实类意图**
  （order_status / asset_summary / workorder_status / risk_restriction / product_status）
  也走知识检索。实测"我的订单 ORD-123456 状态"回退到该节点后，
  用知识库查订单跑了 **20 轮**全失败 → `low_confidence` → **误转人工**；
  而管线用 `get_my_order_status` 一次即得 `fact_not_found` → `needs_clarification`
  且 `requires_handoff=False`。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.agents.graph import SupervisorGraph, _plan_to_dict
from app.schemas.agents import AgentState
from app.services.customer_intent import IntentPlan, IntentTask


def _state(**kw) -> AgentState:
    base = dict(request_id="r1", trace_id="t1", user_message="q",
                customer_id="c1", conversation_id="conv-1")
    base.update(kw)
    return AgentState(**base)


def _one_task_plan(intent: str) -> IntentPlan:
    return IntentPlan(normalized_query="q",
                      tasks=[IntentTask("t1", intent, "x", "q", confidence=.9)])


class RecordingTool:
    """记录工具名；按工具类型返回**真实会出现的**失败原因。

    知识检索失败是 `low_confidence`（RAG 无依据），事实工具失败是 `fact_not_found`（查无此对象）——
    让 fake 返回与真实一致的 error，避免测出不可能的路径。
    """

    def __init__(self):
        self.names: list[str] = []

    async def execute(self, *, name, customer_id, query, intent=None, entities=None):
        self.names.append(name)
        error = "low_confidence" if name == "search_customer_knowledge" else "fact_not_found"
        data = {"evidence_status": "low_confidence"} if error == "low_confidence" else {}
        return SimpleNamespace(success=False, data=data, answer="", error=error)


@pytest.mark.parametrize("intent,expected_tool", [
    ("order_status", "get_my_order_status"),
    ("workorder_status", "get_my_workorder_status"),
    ("asset_summary", "get_my_asset_summary"),
    ("risk_restriction", "get_my_risk_restriction"),
    ("product_status", "get_product_status"),
    ("policy", "search_customer_knowledge"),
    ("faq", "search_customer_knowledge"),
    ("workflow", "search_customer_knowledge"),
    ("company_info", "search_customer_knowledge"),
])
def test_react_step_node_selects_tool_by_intent(intent, expected_tool):
    """事实类意图必须用对应事实工具，知识类才用知识检索。"""
    tool = RecordingTool()
    graph = SupervisorGraph(react_tool=tool)
    state = _state()
    state.plan = _plan_to_dict(_one_task_plan(intent))
    asyncio.run(graph.react_step_node(state))
    assert tool.names == [expected_tool], \
        f"{intent} 应使用 {expected_tool}，实际 {tool.names}"


def _run_react_to_completion(graph, state):
    """驱动 react_step_node 直到产生最终结果（循环内会先做改写重试）。

    react_step_node 每步只推进一轮；`react_result`/`react_results` 仅在完成时置位，
    故需要重复调用（对应图中的条件边回环）。
    """
    patch: dict = {}
    for _ in range(30):
        patch = asyncio.run(graph.react_step_node(state))
        if patch.get("react_results") or patch.get("react_result"):
            return patch
        # 把改写后的 query 与尝试记录写回，模拟条件边回环
        if patch.get("react_query"):
            state.react_query = patch["react_query"]
        if patch.get("react_attempts"):
            state.react_attempts = patch["react_attempts"]
        if patch.get("seen_signatures"):
            state.seen_signatures = patch["seen_signatures"]
        if patch.get("seen_signatures_by_task"):
            state.seen_signatures_by_task = patch["seen_signatures_by_task"]
    return patch


def _final_result(patch: dict) -> dict:
    results = patch.get("react_results") or []
    if results:
        return results[-1]
    return patch.get("react_result") or {}


def test_fact_tool_failure_uses_clarification_not_handoff_wording():
    """事实工具 fact_not_found → "请补充信息"文案，不得用知识检索的转人文案。"""
    tool = RecordingTool()
    graph = SupervisorGraph(react_tool=tool)
    state = _state(user_message="查订单")
    state.plan = _plan_to_dict(_one_task_plan("order_status"))
    finished = _final_result(_run_react_to_completion(graph, state))
    assert "请补充更明确的查询对象" in (finished.get("clarification") or ""), \
        f"事实工具失败应提示补充信息，而非知识检索的转人文案: {finished}"


def test_knowledge_tool_failure_keeps_handoff_wording():
    """知识检索失败仍用 handoff 文案（该走转人工的路径不变）。"""
    tool = RecordingTool()
    graph = SupervisorGraph(react_tool=tool)
    state = _state(user_message="开户流程")
    state.plan = _plan_to_dict(_one_task_plan("workflow"))
    finished = _final_result(_run_react_to_completion(graph, state))
    assert "当前资料不足" in (finished.get("clarification") or ""), f"实际: {finished}"


def test_fact_tool_failure_does_not_trigger_handoff():
    """事实工具失败不得引发转人工信号（管线语义：请用户补充，不转人工）。

    `needs_handoff` 依据 `error == "low_confidence"` 判定，故 fact_not_found 不应触发。
    """
    tool = RecordingTool()
    graph = SupervisorGraph(react_tool=tool)
    state = _state(user_message="查订单")
    state.plan = _plan_to_dict(_one_task_plan("order_status"))
    finished = _final_result(_run_react_to_completion(graph, state))

    assert finished.get("error") == "fact_not_found", f"实际: {finished}"
    assert finished.get("error") != "low_confidence", "fact_not_found 不应被当作转人工信号"
