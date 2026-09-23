import asyncio
from types import SimpleNamespace

import pytest

from app.agents.checkpointer import memory_checkpointer, thread_config
from app.agents.graph import SupervisorGraph, _handoff_reason
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
    assert final["handoff_suggested"] is True
    assert final["handoff_reason"] == "clarification_exhausted"
    # 澄清耗尽也只是「建议」——建单必须等用户确认
    assert not final.get("customer_service_session_id")


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


# ---------- 方案 C：自评不足只降级状态，不再自动设 clarification ----------
#
# 背景：前端 AgentBubble.vue 的条件是
#   v-if="overall_status==='needs_clarification' || clarification"
# 只要 clarification 非空就弹「需要补充信息」。而 `insufficient` 是**模型自己的保守判断**
# （问句有歧义、或对完整性要求高时都会触发），且它通常在答案正文里已自述了不确定之处。
# 故解耦：状态位保留（机器信号），提示不再自动设（不打断用户）。


def _rag_planner(*intents):
    class _Planner:
        def plan(self, query):
            return IntentPlan(
                normalized_query=query,
                tasks=[IntentTask(f"t{i + 1}", it, "rag_explanation", query, confidence=0.9)
                       for i, it in enumerate(intents)])
    return _Planner()


def _run_rag_graph(generator, *intents, user_message="什么是双录"):
    graph = SupervisorGraph(
        planner=_rag_planner(*intents),
        react_tool=FakeReactTool(first_no_citations=False),
        answer_generator=generator).build()
    return asyncio.run(graph.ainvoke(_base_state(user_message=user_message)))


def test_insufficient_answer_downgrades_status_without_prompt():
    """核心行为：状态降级保留，但不再自动设 clarification。"""
    class G:
        async def generate(self, *, query, result):
            return SimpleNamespace(answer="已给出完整数据。", generation_status="generated",
                                   insufficient=True, insufficient_source="explicit")

    final = _run_rag_graph(G(), "policy")
    task = final["task_results"][0]
    assert task["status"] == "partially_answered", "状态位应保留——它是有用的机器信号"
    assert not task.get("clarification"), "方案 C：不得再自动设提示来打断用户"
    assert task["data"]["insufficient_evidence"] is True


def test_insufficient_source_is_exposed_for_troubleshooting():
    """判定来源必须可见，否则无法区分「模型判的」与「关键词撞的」。"""
    class G:
        async def generate(self, *, query, result):
            return SimpleNamespace(answer="a", generation_status="generated",
                                   insufficient=True, insufficient_source="keyword")

    final = _run_rag_graph(G(), "policy")
    assert final["task_results"][0]["data"]["insufficient_source"] == "keyword"


def test_output_text_contains_no_clarification_prompt():
    """最终文本里不应出现中断提示——这是用户直接看到的东西。"""
    class G:
        async def generate(self, *, query, result):
            return SimpleNamespace(answer="公司注册用户数 862 万。", generation_status="generated",
                                   insufficient=True, insufficient_source="explicit")

    final = _run_rag_graph(G(), "policy")
    assert "当前资料未完全覆盖" not in final["final_response"]
    assert "需要补充信息" not in final["final_response"]


def test_complete_answer_stays_completed():
    """正常完整回答不受影响（不能为了修误报把状态整体放宽）。"""
    class G:
        async def generate(self, *, query, result):
            return SimpleNamespace(answer="完整答案", generation_status="generated",
                                   insufficient=False, insufficient_source="explicit")

    final = _run_rag_graph(G(), "policy")
    assert final["task_results"][0]["status"] == "succeeded"
    assert not final["task_results"][0].get("clarification")
    assert final["overall_status"] == "completed"


def test_generator_without_insufficient_attribute_is_not_penalized():
    """生成器没有该属性时（老 provider / fake）不得被当成资料不足。"""
    class G:
        async def generate(self, *, query, result):
            return SimpleNamespace(answer="答案", generation_status="generated")

    final = _run_rag_graph(G(), "policy")
    assert final["task_results"][0]["status"] == "succeeded"
    assert final["overall_status"] == "completed"


def test_mixed_tasks_keep_partial_status_but_no_global_prompt():
    """多 task 混合：一个不足、一个正常。

    整体状态仍应如实反映"有不完整"（partially_answered），
    但没有 task 带 clarification，故整体也不该冒出提示——
    这正是前端不再弹「需要补充信息」的关键。
    """
    class G:
        def __init__(self):
            self.n = 0

        async def generate(self, *, query, result):
            self.n += 1
            return SimpleNamespace(answer=f"答案{self.n}", generation_status="generated",
                                   insufficient=(self.n == 1), insufficient_source="explicit")

    final = _run_rag_graph(G(), "policy", "faq")
    statuses = [t["status"] for t in final["task_results"]]
    assert "partially_answered" in statuses and "succeeded" in statuses, statuses
    assert final["overall_status"] == "partially_answered"
    assert not final.get("clarification"), "无 task 带 clarification → 整体不应有提示"


# ---------- 转人工：一律「建议 + 确认」，任何路径都不得静默建单 ----------
#
# 背景（2026-09-23 实测）：query=「转人工」时前端永远不弹确认卡片，工单与人工会话
# 却已经建好；而答案里同时写着「是否现在转接人工客服？」——系统自己问自己答。
# 根因是建单分支把「没有 low_confidence」当成了「用户主动要求」的**兜底判据**，
# 于是任何其他来源的 needs_handoff 都会落到那一支被静默建单。
# 现在：判定用显式判据（见 _handoff_reason），建单唯一入口是用户点确认后的接口。

class FakeLowConfidenceExecutor:
    async def execute(self, *, customer_id: str, plan: IntentPlan):
        return [
            TaskExecutionResult(t.task_id, t.intent, "failed", answer="",
                                error="low_confidence", data={})
            for t in plan.tasks
        ]


def _handoff_planner(*intents, requires_handoff: bool = False):
    class _Planner:
        def plan(self, query: str):
            return IntentPlan(
                normalized_query=query,
                tasks=[IntentTask(f"t{i + 1}", it, "handoff", query, confidence=0.99)
                       for i, it in enumerate(intents)],
                requires_handoff=requires_handoff)
    return _Planner()


def test_handoff_reason_prefers_explicit_user_request():
    """判据按**具体程度**排序：用户明确表态优先于系统推断。

    末一条是关键回归点——两者皆否时必须落到 clarification_exhausted，
    而不能像修复前那样被当成 customer_requested 去建单。
    """
    requested = IntentPlan(normalized_query="q",
                           tasks=[IntentTask("t1", "human_handoff", "handoff", "q")])
    other = IntentPlan(normalized_query="q",
                       tasks=[IntentTask("t1", "faq", "rag_explanation", "q")])
    assert _handoff_reason(plan=requested, low_confidence=True) == "customer_requested"
    assert _handoff_reason(plan=requested, low_confidence=False) == "customer_requested"
    assert _handoff_reason(plan=other, low_confidence=True) == "low_confidence"
    assert _handoff_reason(plan=other, low_confidence=False) == "clarification_exhausted"


def test_user_requested_handoff_still_needs_confirmation():
    """用户主动说"转人工"也不能直接建单——同样要过确认卡片。"""
    graph = SupervisorGraph(planner=_handoff_planner("human_handoff", requires_handoff=True),
                            executor=FakeExecutor()).build()
    final = asyncio.run(graph.ainvoke(_base_state(user_message="转人工")))

    assert final["handoff_suggested"] is True
    assert final["handoff_reason"] == "customer_requested"
    assert not final.get("work_order_id"), "未确认前不得建工单"
    assert not final.get("ticket_no")
    assert not final.get("customer_service_session_id"), "未确认前不得开人工会话"


def test_low_confidence_only_suggests_handoff():
    """检索低置信：只建议，不建单。"""
    graph = SupervisorGraph(planner=_handoff_planner("faq"),
                            executor=FakeLowConfidenceExecutor()).build()
    final = asyncio.run(graph.ainvoke(_base_state()))

    assert final["handoff_suggested"] is True
    assert final["handoff_reason"] == "low_confidence"
    assert not final.get("customer_service_session_id")


def test_human_handoff_intent_does_not_reach_tool_loop():
    """转人工是确定性意图，不得被送进只读工具轮（会产出错误答案）。"""
    calls = []

    class RecordingTool:
        async def execute(self, *, name, customer_id, query, intent, entities):
            calls.append(name)
            return SimpleNamespace(success=True, error=None, data={}, answer="")

    graph = SupervisorGraph(planner=_handoff_planner("human_handoff", requires_handoff=True),
                            executor=FakeExecutor(), react_tool=RecordingTool()).build()
    asyncio.run(graph.ainvoke(_base_state(user_message="转人工")))
    assert calls == [], f"转人工不应调用知识检索工具，实际调用了 {calls}"
