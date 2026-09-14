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


# ---------- 模型工具轮的意图级门禁（回归：越界导致错误答案） ----------
# ISSUE-20260914-MODEL-TOOL-MISROUTES-NON-RAG-INTENTS：model_tool_node 曾对**所有**查询生效，
# 于是 product_recommendation/greeting/human_handoff 也被丢给知识检索工具，
# 拿到 RAG 短路占位串当答案（"产品推荐使用实时 Product 数据…"）。

@pytest.mark.parametrize("query,expected_intent", [
    ("你好", "greeting"),
    ("推荐适合我的产品", "product_recommendation"),
    ("我想转人工客服", "human_handoff"),
])
def test_dedicated_path_intents_excluded_from_model_tool(query, expected_intent):
    """有专用确定性路径的意图不得进入模型工具轮。

    必须测**实际路由决策**（_route_after_plan），而不只是白名单函数——
    否则路由条件被改回"只看开关"时测试不会失败。
    """
    from app.services.customer_intent import CustomerIntentPlanner
    from app.agents.graph import _plan_to_dict

    plan = CustomerIntentPlanner().plan(query)
    assert [t.intent for t in plan.tasks] == [expected_intent]
    assert SupervisorGraph._model_tool_applicable(plan) is False, \
        f"{expected_intent} 不应进模型工具轮"

    # 端到端：即使用户开了模型工具开关，路由也必须把它导向确定性节点
    supervisor = SupervisorGraph(model_tool_loop_factory=lambda **kw: None, database=object())
    supervisor._model_tool_selection_enabled = lambda: True   # 强制开关为开
    state = _state(user_message=query)
    state.plan = _plan_to_dict(plan)
    assert supervisor._route_after_plan(state) != "model_tool_node", \
        f"{expected_intent} 被误路由到模型工具轮"


@pytest.mark.parametrize("query", [
    "查一下我的持仓", "我的资产有多少", "我有没有风险限制",
    "你们公司的主营业务是什么",
])
def test_readonly_query_intents_allowed_in_model_tool(query):
    """只读查询类意图应保留模型自主工具调度能力。"""
    from app.services.customer_intent import CustomerIntentPlanner
    plan = CustomerIntentPlanner().plan(query)
    assert SupervisorGraph._model_tool_applicable(plan) is True, \
        f"{query} 应走模型工具轮"


def test_unknown_intent_does_not_enter_model_tool():
    """"什么是双录" 规划为 unknown（规则未识别），不应进模型工具轮。

    实测该 query 管线与图均为 needs_clarification（对照 OK），
    门禁把它交回确定性 execute_node 反而与管线一致。
    """
    from app.services.customer_intent import CustomerIntentPlanner
    plan = CustomerIntentPlanner().plan("什么是双录")
    assert [t.intent for t in plan.tasks] == ["unknown"]
    assert SupervisorGraph._model_tool_applicable(plan) is False


def test_model_tool_intents_whitelist_matches_expectation():
    """门禁白名单必须只含只读查询意图。"""
    whitelist = SupervisorGraph.MODEL_TOOL_INTENTS
    forbidden = {"product_recommendation", "greeting", "human_handoff", "business_operation"}
    assert not (whitelist & forbidden), f"白名单混入专用路径意图: {whitelist & forbidden}"


def test_model_tool_requires_enabled_flag():
    """开关关闭时即便意图适用也不应进模型工具轮。"""
    from app.services.customer_intent import CustomerIntentPlanner
    plan = CustomerIntentPlanner().plan("查一下我的持仓")
    supervisor = SupervisorGraph()   # 未注入 factory
    assert supervisor._model_tool_selection_enabled() is False


# ---------- 对照公平性：ReAct 预算与兜底文案必须与管线一致 ----------
# ISSUE-20260914-REACT-BUDGET-AND-PLACEHOLDER-DIVERGENCE：图默认 4/8 而管线 20/20，
# 同一 query 检索尝试次数不同 → 双跑对照本身不公平；且图的兜底文案自造，
# 与管线用的 RAG handoff message 不同源 → answer 字段必然 DIFF（"假差异"）。


def test_graph_react_budget_matches_pipeline():
    """图的 ReAct 预算必须与管线常量一致（否则对照不公平）。"""
    from app.services.customer_task_executor import CustomerTaskExecutor

    graph = SupervisorGraph()
    assert graph.max_iterations == CustomerTaskExecutor.MAX_REACT_ITERATIONS
    assert graph.max_tool_calls == CustomerTaskExecutor.MAX_REACT_TOOL_CALLS


def test_react_budget_defaults_track_pipeline_constant():
    """默认值应**引用**管线常量而非硬编码，防止日后再次漂移。"""
    from app.services.customer_task_executor import CustomerTaskExecutor

    original_iter = CustomerTaskExecutor.MAX_REACT_ITERATIONS
    original_calls = CustomerTaskExecutor.MAX_REACT_TOOL_CALLS
    try:
        CustomerTaskExecutor.MAX_REACT_ITERATIONS = 7
        CustomerTaskExecutor.MAX_REACT_TOOL_CALLS = 9
        graph = SupervisorGraph()
        assert graph.max_iterations == 7, "默认值未跟随管线常量（疑似硬编码）"
        assert graph.max_tool_calls == 9
    finally:
        CustomerTaskExecutor.MAX_REACT_ITERATIONS = original_iter
        CustomerTaskExecutor.MAX_REACT_TOOL_CALLS = original_calls


def test_fallback_placeholder_reuses_tool_message():
    """无证据时 answer 必须沿用工具返回的 RAG handoff message，而非自造占位串。"""
    rag_message = "当前知识库没有足够依据，建议转人工客服或创建工单。"

    class FailingTool:
        async def execute(self, *, name, customer_id, query, intent, entities):
            from types import SimpleNamespace
            return SimpleNamespace(success=False, error="low_confidence",
                                   data={"evidence_status": "low_confidence"},
                                   answer=rag_message)

    class PolicyPlanner:
        def plan(self, query, context=None):
            return IntentPlan(normalized_query=query, tasks=[IntentTask(
                "t1", "policy", "rag_explanation", query, confidence=.8)])

    supervisor = SupervisorGraph(planner=PolicyPlanner(), react_tool=FailingTool())
    final = asyncio.run(supervisor.build().ainvoke(_state(user_message="开户流程是怎样的")))
    task = final["task_results"][0]
    assert task["status"] == "needs_clarification"
    assert task["answer"] == rag_message, "兜底文案未沿用工具的 RAG message（会产生假 DIFF）"


def test_fallback_placeholder_constant_matches_pipeline_wording():
    """工具未返回 message 时的回退常量，必须与管线文案逐字一致。"""
    from app.agents.graph import _FALLBACK_NO_EVIDENCE, _FALLBACK_CLARIFICATION
    from app.services.customer_rag_service import CustomerRagService
    import inspect as _inspect

    src = _inspect.getsource(CustomerRagService)
    assert _FALLBACK_NO_EVIDENCE in src, "回退文案与 CustomerRagService 的 handoff message 不一致"
    assert _FALLBACK_CLARIFICATION in _inspect.getsource(
        __import__("app.services.customer_task_executor", fromlist=["x"])), \
        "回退 clarification 与管线 _bounded_react_knowledge 的占位串不一致"


# ---------- 偏好链路（切流前置项：图此前完全缺失） ----------
# ISSUE-20260914-GRAPH-MISSING-PREFERENCE-CHAIN：管线有「偏好持久化 + 纯偏好短路」两条链路，
# 图此前都没有（grep pure_preference/extract_preference_statement in graph.py → 0）。
# 缺这两条时切流会造成功能退化：用户说"我偏好稳健"不会写入长时记忆，后续推荐无法复用。


class PreferenceRecorder:
    """记录 remember / save_short_term 调用，验证偏好链路副作用。"""

    def __init__(self):
        self.remembered = []
        self.saved = []

    async def load_short_term(self, *, customer_id, conversation_id):
        return {}

    async def save_short_term(self, *, customer_id, conversation_id, state):
        self.saved.append(state)

    async def load_long_term(self, *, customer_id, keys=None):
        return []

    async def remember(self, **kw):
        self.remembered.append(kw)


class SimpleDatabase:
    """最小 database：仅为让 SharedMemoryService 构造成功。

    偏好短路路径不应触达业务 session（session_factory 会抛错以暴露误用）。
    """

    def session_factory(self):
        raise RuntimeError("偏好短路路径不应触达 DB session")


def _pref_graph(memory):
    """构造带偏好链路的图（database 用最小替身）。"""
    supervisor = SupervisorGraph(database=SimpleDatabase())
    supervisor._memory = lambda: memory
    return supervisor.build()


def test_preference_is_persisted_to_long_term_memory():
    """用户自述偏好必须落长时记忆（含 shared 作用域），供后续会话/其它 Agent 复用。"""
    memory = PreferenceRecorder()
    graph = _pref_graph(memory)
    asyncio.run(graph.ainvoke(_state(user_message="我偏好稳健型")))

    keys = [r.get("key") for r in memory.remembered]
    assert "risk_preference" in keys, f"偏好未落库: {keys}"
    entry = next(r for r in memory.remembered if r.get("key") == "risk_preference")
    assert entry["value"] == "balanced"
    assert entry["source"] == "customer_stated"


def test_pure_preference_short_circuits_without_execution():
    """纯偏好自述 → 只确认、不推荐产品清单；route 为推荐但 results 为空。"""
    memory = PreferenceRecorder()
    graph = _pref_graph(memory)
    final = asyncio.run(graph.ainvoke(_state(user_message="我偏好稳健型")))

    assert final["preference_short_circuit"] is True
    assert final["overall_status"] == "needs_clarification"
    assert final["route"] == "customer_recommendation"
    assert final["selected_agents"] == ["CustomerRecommendationCapability"]
    assert final["task_results"] == [], "短路不应执行任何任务"
    assert "已记下您的偏好" in final["final_response"]


def test_pure_preference_resets_clarification_count():
    """短路是"确认"而非"失败澄清"，计数必须归零，否则会累积到误转人工。"""
    memory = PreferenceRecorder()
    graph = _pref_graph(memory)
    asyncio.run(graph.ainvoke(_state(user_message="我偏好稳健型", clarification_count=1)))
    assert memory.saved, "短路也要回写短时记忆"
    assert memory.saved[-1]["clarification_count"] == 0
    assert memory.saved[-1]["pending_intent_plan"] == {}
    assert memory.saved[-1]["handoff"] is None


def test_preference_with_request_marker_does_not_short_circuit():
    """"我偏好稳健，推荐个产品"含请求标记 → 不短路，走正常推荐编排。"""
    memory = PreferenceRecorder()

    class RecommendPlanner:
        def plan(self, query, context=None):
            return IntentPlan(normalized_query=query, tasks=[IntentTask(
                "t1", "product_recommendation", "suitability", query, confidence=.96)])

    class FakeRecommendExecutor:
        async def execute(self, *, customer_id, plan):
            return [TaskExecutionResult(t.task_id, t.intent, "succeeded",
                                        answer="推荐结果", data={}) for t in plan.tasks]

    supervisor = SupervisorGraph(planner=RecommendPlanner(), executor=FakeRecommendExecutor(),
                                 database=SimpleDatabase())
    supervisor._memory = lambda: memory
    final = asyncio.run(supervisor.build().ainvoke(_state(user_message="我偏好稳健，推荐个产品")))

    assert final.get("preference_short_circuit") is not True
    assert final["task_results"], "应正常执行推荐任务"


def test_preference_shared_observation_written_for_other_agents():
    """偏好同时写入 shared 作用域观察，供投顾/推荐等其它 Agent 复用。

    注意：graph.py 内是**函数局部** `from ... import SharedMemoryService`，
    因此必须 patch 源模块 app.services.shared_memory_service，patch graph 模块属性无效。
    """
    import app.services.shared_memory_service as sms_mod
    memory = PreferenceRecorder()
    shared_calls: list = []
    original = sms_mod.SharedMemoryService

    class RecordingShared:
        def __init__(self, database, redis_client=None):
            pass

        async def recall(self, **kw):
            return []

        async def remember(self, **kw):
            shared_calls.append(kw)

    sms_mod.SharedMemoryService = RecordingShared
    try:
        supervisor = SupervisorGraph(database=SimpleDatabase())
        supervisor._memory = lambda: memory
        asyncio.run(supervisor.build().ainvoke(_state(user_message="我喜欢稳健的产品")))
    finally:
        sms_mod.SharedMemoryService = original

    assert shared_calls, "应写入 shared 作用域观察"
    assert shared_calls[0]["scope"] == "shared"
    assert shared_calls[0]["key"] == "product_preference"


def test_staff_call_does_not_short_circuit_or_write_customer_memory():
    """staff/advisor 分析不得把员工当客户落记忆（is_customer=False 时跳过偏好链路）。"""
    memory = PreferenceRecorder()

    class ClearPlanner:
        def plan(self, query, context=None):
            return IntentPlan(normalized_query=query, tasks=[IntentTask(
                "t1", "asset_summary", "business_fact", query, confidence=.9)])

    class FakeExecutor:
        async def execute(self, *, customer_id, plan):
            return [TaskExecutionResult(t.task_id, t.intent, "succeeded", answer="ok", data={})
                    for t in plan.tasks]

    supervisor = SupervisorGraph(planner=ClearPlanner(), executor=FakeExecutor(),
                                 database=SimpleDatabase())
    supervisor._memory = lambda: memory
    final = asyncio.run(supervisor.build().ainvoke(
        _state(user_message="我偏好稳健型", is_customer=False)))

    assert final.get("preference_short_circuit") is not True
    pref_writes = [r for r in memory.remembered
                   if r.get("key") in {"risk_preference", "product_preference"}]
    assert not pref_writes, "员工调用不应写入客户偏好记忆"


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
