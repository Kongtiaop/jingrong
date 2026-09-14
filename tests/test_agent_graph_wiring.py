"""SupervisorGraph 接入测试：节点副作用、状态流转、runner 映射、影子对照。

覆盖三类风险：
1. 图的澄清计数/提前转人工必须与管线语义一致（否则第 2 轮转人工永不触发）。
2. runner 的响应映射必须复原管线的 answer 拼接与 handoff 字段。
3. 影子对照必须只写审计、绝不抛异常穿透真实请求。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agents.graph import SupervisorGraph
from app.agents.checkpointer import memory_checkpointer, thread_config
from app.schemas.agents import AgentState
from app.schemas.customer_service import CustomerTaskExecutionResponse, TaskExecutionResultResponse
from app.services.customer_intent import IntentPlan, IntentTask
from app.services.customer_task_executor import TaskExecutionResult
from app.services.graph_agent_runner import (
    GraphAgentRunner,
    response_from_state,
    shadow_diff,
    state_from_request,
)


class FakeExecutor:
    def __init__(self, results=None):
        self.results = results
        self.seen = []

    async def execute(self, *, customer_id: str, plan: IntentPlan):
        self.seen.append(plan)
        if self.results is not None:
            return self.results
        return [TaskExecutionResult(t.task_id, t.intent, "succeeded", answer=f"已查询{t.intent}", data={})
                for t in plan.tasks]


class FakeMemory:
    """记录 save_short_term / remember 调用，验证 persist 节点副作用。"""

    def __init__(self, short_term=None):
        self.short_term = short_term or {}
        self.saved = []
        self.remembered = []

    async def load_short_term(self, *, customer_id, conversation_id):
        return dict(self.short_term)

    async def save_short_term(self, *, customer_id, conversation_id, state):
        self.saved.append({"customer_id": customer_id, "conversation_id": conversation_id, "state": state})

    async def load_long_term(self, *, customer_id, keys=None):
        return []

    async def remember(self, **kw):
        self.remembered.append(kw)


class FakeDatabase:
    """最小 database：session_factory 可做 async with，用于 OutboxEvent 入队。"""

    def __init__(self):
        self.added = []
        self._self = self

    def session_factory(self):
        return _FakeSessionCtx(self)


class _FakeSessionCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.db.added.append(obj)

    async def commit(self):
        pass


def _state(**kw) -> AgentState:
    base = dict(request_id="r1", trace_id="t1", user_message="查一下我的持仓",
                customer_id="c1", conversation_id="conv-1")
    base.update(kw)
    return AgentState(**base)


class ClarifyPlanner:
    def plan(self, query, context=None):
        return IntentPlan(normalized_query=query, tasks=[], clarification="您想查询哪一类信息？")


class ClearPlanner:
    def plan(self, query, context=None):
        return IntentPlan(normalized_query=query,
                          tasks=[IntentTask("t1", "asset_summary", "business_fact", query, confidence=.9)])


class MultiKnowledgePlanner:
    """"公司业务"同时命中 company_info + company_business（真实规划器会产出两个 task）。"""

    def plan(self, query, context=None):
        return IntentPlan(normalized_query=query, tasks=[
            IntentTask("t1", "company_info", "rag_explanation", query, confidence=.70),
            IntentTask("t2", "company_business", "rag_explanation", query, confidence=.75),
        ])


class FakeKnowledgeTool:
    def __init__(self):
        self.intents = []

    async def execute(self, *, name, customer_id, query, intent, entities):
        self.intents.append(intent)
        return SimpleNamespace(
            success=True, error=None, answer=f"{intent} 的答案",
            data={"evidence_status": "sufficient",
                  "citations": [{"chunk_id": f"{intent}-c1", "content": f"{intent} 内容"}]})


# ---------- 多意图：ReAct 路径不得丢 task（真实回归 bug） ----------

def test_react_loop_covers_all_knowledge_tasks():
    """回归：此前 react_step_node 只取 plan.tasks[0]，多意图时后续 task 被静默丢弃。

    真实案例：query "你们公司的主营业务是什么" 规划出 company_info + company_business，
    管线产出 2 个结果，图只产出 1 个（对照脚本报 task_intents 差异）。
    """
    tool = FakeKnowledgeTool()
    supervisor = SupervisorGraph(planner=MultiKnowledgePlanner(), react_tool=tool)
    graph = supervisor.build()
    final = asyncio.run(graph.ainvoke(_state(user_message="你们公司的主营业务是什么")))

    intents = [r["intent"] for r in final["task_results"]]
    assert intents == ["company_info", "company_business"], f"多意图 task 丢失: {intents}"
    assert tool.intents == ["company_info", "company_business"]


def test_same_query_across_tasks_does_not_false_early_stop():
    """回归：多意图 task 共用同一句 query 时，早停哈希不得跨 task 误判"结果重复"。

    真实案例（"你们公司的主营业务是什么"）：plan 产出 company_info + company_business，
    两个 task 的 query 文本完全相同 → _result_signature 相同 → 若共用 seen_signatures，
    第二个 task 立即 repeated=True → 早停成 needs_clarification →
    summarize 变 partially_completed + requires_handoff=True（把用户错误转人工）。
    修法：早停哈希按 task 隔离（seen_signatures_by_task）。
    """

    class SameQueryPlanner:
        def plan(self, query, context=None):
            return IntentPlan(normalized_query=query, tasks=[
                IntentTask("t1", "company_info", "rag_explanation", query, confidence=.70),
                IntentTask("t2", "company_business", "rag_explanation", query, confidence=.75),
            ])

    supervisor = SupervisorGraph(planner=SameQueryPlanner(), react_tool=FakeKnowledgeTool())
    final = asyncio.run(supervisor.build().ainvoke(_state(user_message="你们公司的主营业务是什么")))

    statuses = [r["status"] for r in final["task_results"]]
    assert statuses == ["succeeded", "succeeded"], f"第二个 task 被误判早停: {statuses}"
    assert final["overall_status"] == "completed"
    assert final["needs_handoff"] is False, "不应因误判早停而把用户转人工"


def test_per_task_signatures_tracked_separately():
    """seen_signatures_by_task 应按 task index 分别记录，供审计定位。"""
    tool = FakeKnowledgeTool()
    supervisor = SupervisorGraph(planner=MultiKnowledgePlanner(), react_tool=tool)
    final = asyncio.run(supervisor.build().ainvoke(_state(user_message="公司业务")))
    by_task = final.get("seen_signatures_by_task") or {}
    assert set(by_task.keys()) == {"0", "1"}, f"应为每个 task 独立记录: {by_task}"


def test_react_generate_covers_all_knowledge_tasks():
    """多意图下每个 task 都要经过 LLM 生成，不能只有第一个有答案。"""

    class FakeGenerator:
        def __init__(self):
            self.queries = []

        async def generate(self, *, query, result):
            self.queries.append(result.intent)
            return SimpleNamespace(answer=f"生成:{result.intent}", generation_status="generated")

    gen = FakeGenerator()
    supervisor = SupervisorGraph(planner=MultiKnowledgePlanner(), react_tool=FakeKnowledgeTool(),
                                 answer_generator=gen)
    graph = supervisor.build()
    final = asyncio.run(graph.ainvoke(_state(user_message="公司业务")))

    assert sorted(gen.queries) == ["company_business", "company_info"]
    assert all(r["answer"].startswith("生成:") for r in final["task_results"])


def test_single_knowledge_task_unchanged_by_multi_intent_support():
    """单 task 路径行为不变（向后兼容）。"""

    class SingleKnowledgePlanner:
        def plan(self, query, context=None):
            return IntentPlan(normalized_query=query,
                              tasks=[IntentTask("t1", "policy", "rag_explanation", query, confidence=.8)])

    gen_calls = []

    class Gen:
        async def generate(self, *, query, result):
            gen_calls.append(1)
            return SimpleNamespace(answer="单意图答案", generation_status="generated")

    supervisor = SupervisorGraph(planner=SingleKnowledgePlanner(), react_tool=FakeKnowledgeTool(),
                                 answer_generator=Gen())
    final = asyncio.run(supervisor.build().ainvoke(_state(user_message="什么是双录")))
    assert len(final["task_results"]) == 1
    assert final["task_results"][0]["answer"] == "单意图答案"
    assert len(gen_calls) == 1


# ---------- 澄清计数语义（与管线对齐的核心风险点） ----------

def test_clarify_increments_count_and_persists():
    graph = SupervisorGraph(planner=ClarifyPlanner(), executor=FakeExecutor()).build()
    final = asyncio.run(graph.ainvoke(_state(clarification_count=0)))
    assert final["clarification_count"] == 1
    assert final["overall_status"] == "needs_clarification"
    assert final["clarification"]  # persist 需要它判断 missing_parameter


def test_clarify_twice_escalates_to_handoff_node():
    """clarification_count=1 时再来一次澄清 → 直接 handoff（读上一轮计数，跨轮语义正确）。"""
    graph = SupervisorGraph(planner=ClarifyPlanner(), executor=FakeExecutor()).build()
    final = asyncio.run(graph.ainvoke(_state(clarification_count=1)))
    assert final["overall_status"] == "handoff"
    assert final["needs_handoff"] is True


def test_clarification_count_comes_from_short_term():
    """短时记忆里的计数优先于传入 state：保证跨请求轮次累加。"""
    memory = FakeMemory(short_term={"clarification_count": 1, "original_query": "我想查东西"})
    supervisor = SupervisorGraph(planner=ClarifyPlanner(), executor=FakeExecutor(), database=FakeDatabase())
    supervisor._memory = lambda: memory  # 注入 fake 记忆（必须在 build 前：build 返回的是编译图）
    graph = supervisor.build()
    final = asyncio.run(graph.ainvoke(_state(clarification_count=0)))
    assert final["clarification_count"] == 2  # 1(记忆) + 1(本轮)
    assert final["overall_status"] == "handoff"


def test_pending_plan_concatenates_original_query():
    """澄清补答：original_query + 本轮输入 拼接后再规划，否则规划器看不到原问题。"""
    captured = {}

    class CapturePlanner:
        def plan(self, query, context=None):
            captured["query"] = query
            return IntentPlan(normalized_query=query,
                              tasks=[IntentTask("t1", "asset_summary", "business_fact", query, confidence=.9)])

    memory = FakeMemory(short_term={"original_query": "我想查产品",
                                    "pending_intent_plan": {"tasks": [{"task_id": "t1"}]}})
    supervisor = SupervisorGraph(planner=CapturePlanner(), executor=FakeExecutor(), database=FakeDatabase())
    supervisor._memory = lambda: memory
    graph = supervisor.build()
    asyncio.run(graph.ainvoke(_state(user_message="现金管理计划")))
    assert captured["query"] == "我想查产品 现金管理计划"


# ---------- clarify / handoff 的持久化副作用 ----------

def test_clarify_node_reaches_persist():
    memory = FakeMemory()
    db = FakeDatabase()
    supervisor = SupervisorGraph(planner=ClarifyPlanner(), executor=FakeExecutor(), database=db)
    supervisor._memory = lambda: memory
    graph = supervisor.build()
    final = asyncio.run(graph.ainvoke(_state()))
    assert final["persisted"] is True
    assert memory.saved, "澄清轮必须回写短时记忆，否则计数丢失"
    assert memory.saved[-1]["state"]["clarification_count"] == 1
    assert memory.saved[-1]["state"]["missing_parameter"] is True


def test_execute_path_persists_and_enqueues_summary():
    memory = FakeMemory()
    db = FakeDatabase()
    supervisor = SupervisorGraph(planner=ClearPlanner(), executor=FakeExecutor(), database=db)
    supervisor._memory = lambda: memory
    graph = supervisor.build()
    final = asyncio.run(graph.ainvoke(_state()))
    assert final["overall_status"] == "completed"
    assert final["persisted"] is True
    assert memory.saved
    assert any(type(o).__name__ == "OutboxEvent" and o.event_type == "memory.summary_extract" for o in db.added)


def test_execute_node_receives_conversation_context():
    """图必须把 conversation_summary 注入执行器，否则生成质量与管线不等价。"""
    from app.services.customer_task_executor import CustomerTaskExecutor as RealExecutor
    seen = {}

    class CapturingExecutor(RealExecutor):
        def __init__(self, database=None, conversation_summary="", **kw):
            seen["summary"] = conversation_summary
            self.database = database

        async def execute(self, *, customer_id, plan):
            return [TaskExecutionResult(t.task_id, t.intent, "succeeded", answer="ok", data={}) for t in plan.tasks]

    import app.agents.graph as graph_mod
    original = graph_mod.CustomerTaskExecutor
    graph_mod.CustomerTaskExecutor = CapturingExecutor
    try:
        db = FakeDatabase()
        supervisor = SupervisorGraph(planner=ClearPlanner(), database=db)
        supervisor._memory = lambda: FakeMemory()
        graph = supervisor.build()
        state = _state(conversation_context="用户近期在关注稳健产品")
        asyncio.run(graph.ainvoke(state))
        assert seen["summary"] == "用户近期在关注稳健产品"
    finally:
        graph_mod.CustomerTaskExecutor = original


# ---------- runner 响应映射 ----------

def _pipeline_response(**kw) -> CustomerTaskExecutionResponse:
    base = dict(conversation_id="conv-1", overall_status="completed", answer="答案",
                route="analysis", agents=["DataAnalysisAgent"], results=[])
    base.update(kw)
    return CustomerTaskExecutionResponse(**base)


def test_response_from_state_maps_handoff_ticket():
    state = {
        "overall_status": "handoff", "route": "customer_service",
        "selected_agents": ["CustomerServiceAgent"],
        "final_response": "为避免反复追问，已为您转接人工客服。",
        "needs_handoff": True, "ticket_no": "WO-123", "work_order_id": "wo-1",
        "customer_service_session_id": "cs-1", "customer_service_session_status": "waiting_human",
        "task_results": [],
    }
    resp = response_from_state(state=state, conversation_id="conv-1")
    assert resp.requires_handoff is True
    assert resp.ticket_no == "WO-123"
    assert resp.work_order_id == "wo-1"
    assert resp.customer_service_session_id == "cs-1"
    assert "WO-123" in resp.answer  # 工单号拼进 answer，与管线一致


def test_response_from_state_maps_task_results():
    state = {
        "overall_status": "completed", "route": "customer_service",
        "selected_agents": ["CustomerServiceAgent"], "final_response": "聚合答案",
        "task_results": [{"task_id": "t1", "intent": "asset_summary", "status": "succeeded",
                          "answer": "持仓 1000", "data": {"total": 1000}, "error": None, "clarification": None}],
    }
    resp = response_from_state(state=state, conversation_id="conv-1")
    assert resp.results[0].intent == "asset_summary"
    assert resp.results[0].data == {"total": 1000}
    assert resp.answer == "聚合答案"


def test_response_from_state_falls_back_when_empty():
    resp = response_from_state(state={"overall_status": "needs_clarification"}, conversation_id="c")
    assert resp.answer == "当前没有可返回的结果，建议转人工客服。"
    assert resp.agents == ["CustomerServiceAgent"]


def test_state_from_request_sets_conversation_id_for_thread():
    st = state_from_request(customer_id="c1", conversation_id="conv-9", query="你好", is_customer=True)
    assert st.conversation_id == "conv-9"
    assert st.customer_id == "c1"
    assert st.is_customer is True


# ---------- 影子对照 ----------

def test_shadow_diff_detects_status_and_route_changes():
    pipeline = _pipeline_response(overall_status="completed", route="analysis", answer="A")
    graph = _pipeline_response(overall_status="needs_clarification", route="customer_service", answer="B")
    diff = shadow_diff(pipeline, graph)
    assert "overall_status" in diff and "route" in diff and "answer" in diff
    assert diff["overall_status"] == {"pipeline": "completed", "graph": "needs_clarification"}


def test_shadow_diff_empty_when_equivalent():
    pipeline = _pipeline_response()
    assert shadow_diff(pipeline, _pipeline_response()) == {}


def test_shadow_diff_ignores_non_compared_fields():
    """agent 列表差异不在比对范围内（权限过滤发生在图外），不应误报。"""
    pipeline = _pipeline_response(agents=["CustomerServiceAgent"], available_agents=["X"])
    graph = _pipeline_response(agents=["Other"], available_agents=[])
    assert shadow_diff(pipeline, graph) == {}


def test_shadow_writes_audit_and_swallows_errors():
    """影子必须写审计且绝不抛异常：图炸了也不能影响已决定返回的管线结果。"""
    db = FakeDatabase()

    class BoomGraph:
        async def ainvoke(self, *a, **kw):
            raise RuntimeError("graph exploded")

    runner = GraphAgentRunner(db, None, settings=SimpleNamespace(
        agent_graph_max_iterations=4, agent_graph_max_tool_calls=8))
    runner._graph = BoomGraph()
    diff = asyncio.run(runner.run_shadow(
        customer_id="c1", conversation_id="conv-1", query="q",
        pipeline_result=_pipeline_response()))
    assert diff is None  # 未穿透
    assert any(getattr(o, "action", "") == "agent.graph_shadow_error" for o in db.added)


def test_shadow_writes_match_audit_when_equivalent():
    db = FakeDatabase()
    graph_result = _pipeline_response()

    class StubRunner(GraphAgentRunner):
        async def execute(self, **kw):
            return graph_result

    runner = StubRunner(db, None, settings=SimpleNamespace(
        agent_graph_max_iterations=4, agent_graph_max_tool_calls=8))
    diff = asyncio.run(runner.run_shadow(
        customer_id="c1", conversation_id="conv-1", query="q",
        pipeline_result=_pipeline_response()))
    assert diff == {}
    assert any(getattr(o, "action", "") == "agent.graph_shadow_match" for o in db.added)


# ---------- 断点续跑（MemorySaver） ----------

def test_persist_node_runs_after_summarize_on_resume():
    cp = memory_checkpointer()
    graph = SupervisorGraph(planner=ClearPlanner(), executor=FakeExecutor()).build(
        checkpointer=cp, interrupt_before=["summarize_node"])
    cfg = thread_config("thread-persist-1")
    first = asyncio.run(graph.ainvoke(_state(), cfg))
    assert first.get("task_results")  # 中断在 summarize 前
    resumed = asyncio.run(graph.ainvoke(None, cfg))
    assert resumed["overall_status"] == "completed"
    assert "persist_node" in resumed.get("__next__", ()) or resumed.get("persisted") is not None
