import asyncio
from types import SimpleNamespace

import pytest

from app.agents.checkpointer import memory_checkpointer, thread_config
from app.agents.graph import SupervisorGraph
from app.schemas.agents import AgentState
from app.services.customer_intent import IntentPlan, IntentTask
from app.services.customer_task_executor import TaskExecutionResult


class FakeExecutor:
    async def execute(self, *, customer_id: str, plan: IntentPlan):
        # 每个任务返回 succeeded，验证 execute→summarize 链路
        return [
            TaskExecutionResult(t.task_id, t.intent, "succeeded",
                                answer=f"已查询{t.intent}相关结果", data={})
            for t in plan.tasks
        ]


class FakeReactTool:
    """只读知识工具 fake：首次 low_confidence 无引用，第二次有引用（触发 ReAct 回环再停）。"""

    def __init__(self, first_no_citations=True):
        self.calls = 0
        self.first_no_citations = first_no_citations

    async def execute(self, *, name, customer_id, query, intent, entities):
        self.calls += 1
        if self.first_no_citations and self.calls == 1:
            return SimpleNamespace(success=False, error="low_confidence",
                                   data={"evidence_status": "low_confidence"}, answer="暂无足够资料")
        return SimpleNamespace(success=True, error=None,
                               data={"evidence_status": "sufficient",
                                     "citations": [{"chunk_id": "c1", "content": "知识内容"}]},
                               answer="已检索到相关知识")


def _base_state(**kw) -> AgentState:
    base = dict(request_id="r1", trace_id="t1", user_message="查一下我的持仓", customer_id="c1",
                clarification_count=0)
    base.update(kw)
    return AgentState(**base)


def test_graph_compiles_with_clear_query():
    graph = SupervisorGraph(executor=FakeExecutor()).build()
    # 编译不抛错即通过
    assert graph is not None


def test_clear_query_runs_intent_route_execute_summarize():
    graph = SupervisorGraph(executor=FakeExecutor()).build()
    final = asyncio.run(graph.ainvoke(_base_state()))
    assert final["route"] in {"analysis", "customer_service"}  # 持仓→analysis
    assert final["selected_agents"]
    assert final["task_results"]  # 经过了 execute
    assert final["overall_status"] == "completed"  # CustomerTaskExecutor.summarize 返回 completed
    assert final["final_response"]


def test_clarification_query_branches_to_clarify():
    # 制造需要澄清的 query（plan.clarification 非空）→ 走 clarify_node，不进 execute
    class ClarifyPlanner:
        def plan(self, query: str):
            return IntentPlan(normalized_query=query,
                              tasks=[IntentTask("t1", "unknown", "handoff", query, confidence=0.2)],
                              clarification="请描述您需要查询或办理的事项。")

    graph = SupervisorGraph(planner=ClarifyPlanner(), executor=FakeExecutor()).build()
    final = asyncio.run(graph.ainvoke(_base_state()))
    assert final["overall_status"] == "needs_clarification"
    assert final["clarification_count"] == 1
    assert not final["task_results"]  # 澄清分支不应执行任务
    assert final["final_response"]  # 澄清话术


def test_clarification_twice_handoff():
    class ClarifyPlanner:
        def plan(self, query: str):
            return IntentPlan(normalized_query=query,
                              tasks=[IntentTask("t1", "unknown", "handoff", query, confidence=0.2)],
                              clarification="请补充信息。")

    graph = SupervisorGraph(planner=ClarifyPlanner(), executor=FakeExecutor()).build()
    # 第 2 次澄清（count=1 → +1 = 2）→ handoff
    final = asyncio.run(graph.ainvoke(_base_state(clarification_count=1)))
    assert final["overall_status"] == "handoff"
    assert final["needs_handoff"] is True


def test_readonly_knowledge_intent_uses_react_loop():
    # 只读知识意图（company_info）→ 走 LangGraph ReAct 条件循环
    class ReactPlanner:
        def plan(self, query: str):
            return IntentPlan(normalized_query=query,
                              tasks=[IntentTask("t1", "company_info", "rag_explanation", query, confidence=0.9)])

    tool = FakeReactTool()
    graph = SupervisorGraph(planner=ReactPlanner(), react_tool=tool).build()
    final = asyncio.run(graph.ainvoke(_base_state()))
    assert tool.calls >= 2          # 第一次 low_confidence → 回环改写再试
    assert final["react_attempts"]  # 记录了多轮工具调用
    assert final["task_results"] and final["task_results"][0]["status"] == "succeeded"
    assert final["overall_status"] == "completed"


def test_high_stakes_intent_uses_deterministic_execute():
    # 高安全（business_operation 交易草稿）→ 走确定性 execute_node，不触发 ReAct 循环
    class TradePlanner:
        def plan(self, query: str):
            return IntentPlan(normalized_query=query,
                              tasks=[IntentTask("t1", "business_operation", "draft_operation", query, confidence=0.98)])

    graph = SupervisorGraph(planner=TradePlanner(), executor=FakeExecutor(), react_tool=FakeReactTool()).build()
    final = asyncio.run(graph.ainvoke(_base_state(user_message="我要买1000元稳健产品")))
    assert final["route"] == "customer_trade_draft"
    assert not final["react_attempts"]  # 交易意图不进 ReAct 循环
    assert final["task_results"][0]["intent"] == "business_operation"


def test_readonly_react_chain_generates_answer():
    # 只读 ReAct：react 命中引用 → generate_node 调 LLM 生成答案 → summarize
    class ReactPlanner:
        def plan(self, query: str):
            return IntentPlan(normalized_query=query,
                              tasks=[IntentTask("t1", "policy", "rag_explanation", query, confidence=0.9)])

    class FakeGenerator:
        def __init__(self):
            self.calls = 0

        async def generate(self, *, query, result):
            self.calls += 1
            assert result.citations  # 命中引用才生成
            return SimpleNamespace(answer="根据资料，双录指录音录像。", generation_status="generated")

    gen = FakeGenerator()
    tool = FakeReactTool()
    graph = SupervisorGraph(planner=ReactPlanner(), react_tool=tool, answer_generator=gen).build()
    final = asyncio.run(graph.ainvoke(_base_state(user_message="什么是双录")))
    assert gen.calls == 1
    assert final["task_results"][0]["data"].get("generation_status") == "generated"
    assert "双录" in final["final_response"] or final["task_results"][0]["answer"] == "根据资料，双录指录音录像。"


def test_checkpoint_resume_with_memory_saver():
    """断点续跑：MemorySaver checkpointer + summarize 前中断 → 恢复继续执行到完成。"""
    cp = memory_checkpointer()
    graph = SupervisorGraph(executor=FakeExecutor()).build(checkpointer=cp, interrupt_before=["summarize_node"])
    cfg = thread_config("thread-checkpoint-1")

    first = asyncio.run(graph.ainvoke(_base_state(), cfg))
    # 中断在 summarize 之前：有 task_results，但还没 final_response
    assert first.get("task_results") or first.get("react_attempts") or first.get("plan")
    # 恢复：用 None 输入 + 同一 thread_id 续跑
    resumed = asyncio.run(graph.ainvoke(None, cfg))
    assert resumed["overall_status"] == "completed"
    assert resumed["final_response"]
    # 同一 thread 状态被持久化（task_results 仍在）
    assert resumed.get("task_results")


def test_checkpoint_compile_without_checkpointer_is_plain():
    # 不传 checkpointer 仍可正常编译/运行（向后兼容）
    graph = SupervisorGraph(executor=FakeExecutor()).build()
    final = asyncio.run(graph.ainvoke(_base_state()))
    assert final["overall_status"] == "completed"
