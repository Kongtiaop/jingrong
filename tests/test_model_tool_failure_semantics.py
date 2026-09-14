# -*- coding: utf-8 -*-
"""模型工具轮的失败语义必须与管线对齐。

ISSUE-20260914-FAILED-VS-CLARIFICATION-MISMATCH：
  实测"开户流程是怎样的"（知识类 workflow）：
    pipeline task = needs_clarification (error=low_confidence)
    graph    task = failed               (error=low_confidence)   ← 不该是 failed
  两边**都已转人工**，仅状态标记不同；但 `failed` 会污染 `overall_status`
  （`needs_clarification` vs `failed`）并让对照出现假差异。

  根因：`customer_model_tools` 的失败映射只覆盖 `fact_not_found`/`ambiguous_product`，
  **遗漏了知识检索的 `low_confidence`**，后者落到 `failed`。
  而管线两处都把 `low_confidence` 视为 `needs_clarification`
  （`_execute_one` 与 `_bounded_react_knowledge`）。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.services.customer_model_tools import CustomerModelToolLoop


class ScriptedProvider:
    def __init__(self, decisions):
        self._client = object()
        self._decisions = list(decisions)

    async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
        return self._decisions.pop(0) if self._decisions else None


class FailingTools:
    def __init__(self, error, answer="", data=None):
        self.error = error
        self.answer = answer
        self.data = data or {}

    async def execute(self, *, name, customer_id, query, intent=None, entities=None):
        return SimpleNamespace(success=False, data=self.data, answer=self.answer, error=self.error)


class FakeDatabase:
    def session_factory(self):
        raise RuntimeError("no audit in test")


def _run(error, *, tool="search_customer_knowledge", arguments=None, answer="", data=None,
         planned_intents=None):
    """构造一次失败的模型工具调用。

    planned_intents 需与该工具匹配（新增的工具范围约束会拦截计划外工具）：
    search_customer_knowledge ← 知识类意图；get_my_order_status ← order_status。
    """
    decision = {"name": tool, "arguments": arguments or {"query": "q", "intent": "workflow"}}
    if planned_intents is None:
        planned_intents = (
            ["order_status"] if tool == "get_my_order_status" else ["workflow"])
    loop = CustomerModelToolLoop(
        provider=ScriptedProvider([decision, None]),
        tools=FailingTools(error, answer=answer, data=data),
        database=FakeDatabase(), max_rounds=2)
    return asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                query="开户流程是怎样的", planned_intents=planned_intents))


@pytest.mark.parametrize("error", ["fact_not_found", "ambiguous_product", "low_confidence"])
def test_recoverable_errors_map_to_needs_clarification(error):
    """三类"可恢复"错误都应对齐为 needs_clarification（与管线一致）。"""
    outcome = _run(error)
    assert outcome.results, f"{error} 应产出 task 结果"
    assert outcome.results[0]["status"] == "needs_clarification", \
        f"{error} 不应映射为 failed（管线语义是 needs_clarification）"


def test_low_confidence_uses_knowledge_clarification_wording():
    """low_confidence（知识检索无依据）应给知识类澄清文案，而非"补充查询对象"。"""
    outcome = _run("low_confidence",
                   answer="当前知识库没有足够依据，建议转人工客服或创建工单。")
    clarification = outcome.results[0]["clarification"]
    assert clarification == "当前资料不足，可以补充问题或转人工客服。", \
        f"文案应与管线一致，实际 {clarification!r}"


def test_fact_not_found_keeps_fact_clarification_wording():
    """fact_not_found（事实工具查无对象）仍用"补充查询对象"文案。"""
    outcome = _run("fact_not_found", tool="get_my_order_status",
                   arguments={"query": "ORD-1"})
    assert "请补充更明确的查询对象" in (outcome.results[0]["clarification"] or "")


def test_unrecoverable_error_still_maps_to_failed():
    """非可恢复错误（如 no_tool_mapping）仍应为 failed——不要把一切错误都放宽。"""
    outcome = _run("no_tool_mapping")
    assert outcome.results[0]["status"] == "failed"


def test_clarification_constant_matches_pipeline_wording():
    """本地澄清常量须与 graph.py / customer_task_executor 的文案逐字一致。"""
    from app.services.customer_model_tools import _FALLBACK_CLARIFICATION
    from app.agents.graph import _FALLBACK_CLARIFICATION as GRAPH_CONST
    assert _FALLBACK_CLARIFICATION == GRAPH_CONST, \
        "同一语义出现两种文案会造成假差异"
