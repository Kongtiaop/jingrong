# -*- coding: utf-8 -*-
"""工具范围约束 + 生成温度稳定性。

ISSUE-20260914-MODEL-CALLS-OFFPLAN-TOOL（稳定复现）：
  实测"我的工单进展如何"（计划只有 `workorder_status`），模型拿到工单状态后**自行多调**
  `explain_workorder_status` 去"解释状态"，该调用失败（知识库无流程资料）→ 污染整体状态：
```
[graph] task=model-tool-1 intent=workorder_status        succeeded
[graph] task=model-tool-2 intent=explain_workorder_status needs_clarification (low_confidence)
=> partially_completed + requires_handoff=True
answer = '您最近的工单 WO… 状态 open。\n当前知识库没有足够依据，建议转人工客服…'
```
用户已拿到工单状态，却被回复"没有依据、已转人工"——**自相矛盾**。
`explain_*` 本应由规划器通过**多意图 + depends_on** 触发（`CustomerTaskExecutor._execute_one:243`），
不该由模型临时决定。

ISSUE-20260914-GENERATION-TEMPERATURE-INSTABILITY：
  生成温度 0.1 导致同一 query 两次生成出现**事实相反**断言（"是的，您有风险限制" vs "无法判断"）。
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.services.customer_model_tools import CustomerModelToolLoop, _tools_for_intents


class ScriptedProvider:
    def __init__(self, decisions):
        self._client = object()
        self._decisions = list(decisions)
        self.calls = 0

    async def request_tool_call(self, *, system, user, tools, model=None, temperature=0.0):
        self.calls += 1
        return self._decisions.pop(0) if self._decisions else None


class RecordingTools:
    def __init__(self):
        self.names: list[str] = []

    async def execute(self, *, name, customer_id, query, intent=None, entities=None):
        self.names.append(name)
        return SimpleNamespace(success=True, data={}, answer=f"{name} ok", error=None)


class FakeDatabase:
    def __init__(self):
        self.audits = []

    def session_factory(self):
        return _Ctx(self)


class _Ctx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.db.audits.append(obj)

    async def commit(self):
        pass


def test_tools_for_intents_maps_planned_intents():
    assert _tools_for_intents(["workorder_status"]) == {"get_my_workorder_status"}
    assert _tools_for_intents(["company_info", "risk_public"]) == {"search_customer_knowledge"}
    assert _tools_for_intents(None) == set(), "无计划时不约束（向后兼容）"
    assert _tools_for_intents([]) == set()


def test_offplan_tool_is_blocked():
    """模型调用计划外工具时应被拦截，不执行、不产出结果。"""
    tools = RecordingTools()
    db = FakeDatabase()
    provider = ScriptedProvider([
        {"name": "get_my_workorder_status", "arguments": {"query": "我的工单"}},
        {"name": "explain_workorder_status", "arguments": {"query": "解释"}},   # 计划外
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools, database=db, max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                   query="我的工单进展如何",
                                   planned_intents=["workorder_status"]))

    assert tools.names == ["get_my_workorder_status"], \
        f"计划外工具不得执行，实际 {tools.names}"
    assert [r["intent"] for r in outcome.results] == ["workorder_status"]
    assert outcome.stopped_reason == "tool_out_of_plan_scope"
    assert any("tool_out_of_plan_scope" in row.detail for row in db.audits), "应留审计"


def test_workorder_query_matches_pipeline_semantics():
    """"我的工单进展如何" 应只产出一个 task、成功、不转人工（与管线一致）。"""
    tools = RecordingTools()
    provider = ScriptedProvider([
        {"name": "get_my_workorder_status", "arguments": {"query": "我的工单"}},
        {"name": "explain_workorder_status", "arguments": {"query": "解释"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools,
                                 database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                   query="我的工单进展如何",
                                   planned_intents=["workorder_status"]))
    assert len(outcome.results) == 1
    assert outcome.results[0]["status"] == "succeeded"


def test_multi_intent_plan_allows_both_tools():
    """多意图计划时，两个计划内工具都应可用（约束不误伤正常多意图）。"""
    assert _tools_for_intents(["workorder_status", "workflow"]) == {
        "get_my_workorder_status", "search_customer_knowledge"}

    tools = RecordingTools()
    provider = ScriptedProvider([
        {"name": "get_my_workorder_status", "arguments": {"query": "工单"}},
        {"name": "search_customer_knowledge", "arguments": {"query": "流程", "intent": "workflow"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools,
                                 database=FakeDatabase(), max_rounds=4)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1",
                                   query="我的工单进展以及处理流程",
                                   planned_intents=["workorder_status", "workflow"]))
    assert tools.names == ["get_my_workorder_status", "search_customer_knowledge"]
    assert len(outcome.results) == 2


def test_no_scope_constraint_when_plan_absent():
    """未传 planned_intents 时不施加范围约束（保持既有行为与测试兼容）。"""
    tools = RecordingTools()
    provider = ScriptedProvider([
        {"name": "get_my_workorder_status", "arguments": {"query": "工单"}},
        None,
    ])
    loop = CustomerModelToolLoop(provider=provider, tools=tools,
                                 database=FakeDatabase(), max_rounds=2)
    outcome = asyncio.run(loop.run(customer_id="c1", conversation_id="v1", query="工单"))
    assert tools.names == ["get_my_workorder_status"]
    assert outcome.results


def test_generation_temperature_is_zero():
    """生成温度必须为 0：本任务是基于引用复述事实，随机性会造成事实相反的断言。"""
    import inspect
    from app.services.customer_rag_answer import CustomerRagAnswerService

    src = inspect.getsource(CustomerRagAnswerService.generate)
    assert "temperature=0.0" in src, \
        "生成温度应为 0.0（0.1 曾导致同一 query 出现事实相反的断言）"
    assert "temperature=0.1" not in src


# ---- ISSUE-20260914-MODEL-DECISION-REASONING-DISCARDED ----
# 模型发起 tool_call 时**同时给出**的文本（message.content）此前被 provider 丢弃，
# 导致"模型为什么多调一个工具"无法从留痕判断，只能反复猜测。
# 修复：provider 返回 reasoning，循环记入 decisions，并随响应 data.model_tool_trace 带出。

def test_offplan_decision_records_model_reasoning():
    """被拦下的越界调用必须留下模型自述理由——这是判断"有意补充"还是"误判"的唯一直接证据。"""
    tools = RecordingTools()
    provider = ScriptedProvider([
        {"name": "get_my_workorder_status", "arguments": {"query": "我的工单"},
         "reasoning": "先取工单状态"},
        {"name": "explain_workorder_status", "arguments": {"query": "解释"},
         "reasoning": "用户问的是进展，需要解释状态含义"},          # 越界
    ])
    outcome = asyncio.run(CustomerModelToolLoop(
        provider=provider, tools=tools, database=FakeDatabase(), max_rounds=4
    ).run(customer_id="c1", conversation_id="v1", query="我的工单进展如何",
          planned_intents=["workorder_status"]))

    assert [d["outcome"] for d in outcome.decisions] == ["executed", "rejected_off_plan"]
    assert outcome.decisions[1]["tool"] == "explain_workorder_status"
    assert outcome.decisions[1]["reasoning"] == "用户问的是进展，需要解释状态含义"
    assert outcome.decisions[0]["reasoning"] == "先取工单状态"


def test_reasoning_is_audited_to_db():
    """reasoning 必须落审计，否则线上排查还是要靠猜。"""
    db = FakeDatabase()
    provider = ScriptedProvider([
        {"name": "get_my_workorder_status", "arguments": {"query": "我的工单"},
         "reasoning": "取工单状态"},
        None,
    ])
    asyncio.run(CustomerModelToolLoop(
        provider=provider, tools=RecordingTools(), database=db, max_rounds=2
    ).run(customer_id="c1", conversation_id="v1", query="我的工单进展如何",
          planned_intents=["workorder_status"]))

    details = "\n".join(row.detail for row in db.audits)
    assert "取工单状态" in details, f"审计缺少模型自述理由：{details}"


def test_decision_trace_survives_when_reasoning_absent():
    """模型可能只返回 tool_call 不带文本；缺 reasoning 不得影响控制流。"""
    provider = ScriptedProvider([
        {"name": "get_my_workorder_status", "arguments": {"query": "工单"}},
        None,
    ])
    outcome = asyncio.run(CustomerModelToolLoop(
        provider=provider, tools=RecordingTools(), database=FakeDatabase(), max_rounds=2
    ).run(customer_id="c1", conversation_id="v1", query="我的工单进展如何",
          planned_intents=["workorder_status"]))
    assert outcome.decisions[0]["reasoning"] == ""
    assert outcome.results, "缺 reasoning 不应影响结果产出"


def test_response_carries_model_tool_trace():
    """轨迹必须随响应带出：否则图外（API/排查脚本）看不到模型决策过程。"""
    from app.services.graph_agent_runner import response_from_state

    state = {
        "overall_status": "completed",
        "final_response": "答案",
        "task_results": [{
            "task_id": "model-tool-1", "intent": "workorder_status",
            "status": "succeeded", "answer": "工单状态 open", "data": {},
            "error": None, "clarification": None,
        }],
        "model_tool_rounds": [{"round_index": 1, "tool": "get_my_workorder_status"}],
        "model_tool_decisions": [{"round": 1, "tool": "get_my_workorder_status",
                                  "reasoning": "取工单状态", "outcome": "executed"}],
        "model_tool_stopped_reason": "no_tool_call",
    }
    resp = response_from_state(state=state, conversation_id="v1")
    trace = resp.results[0].data["model_tool_trace"]
    assert trace["stopped_reason"] == "no_tool_call"
    assert trace["decisions"][0]["reasoning"] == "取工单状态"

    # 确定性路径（无模型工具轮）不得被塞入空轨迹
    plain = response_from_state(state={"overall_status": "completed",
                                       "task_results": state["task_results"]},
                                conversation_id="v1")
    assert "model_tool_trace" not in plain.results[0].data
