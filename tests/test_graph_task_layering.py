# -*- coding: utf-8 -*-
"""graph 按 depends_on 分层执行：事实必须传给后续层，且不得污染检索串。

ISSUE-20260914-GRAPH-MODEL-TOOL-IGNORES-DEPENDS-ON

背景：`SupervisorGraph.model_tool_node` 此前把 `plan.tasks` 压成**一串意图名**
一次性交给模型循环，`depends_on` 完全不可见 —— 依赖既不参与调度，前序答案也不传递。
实测后果：`我有没有风险限制`（C4 客户）pipeline 正确答出"可投资 R1-R4"，
graph 却输出"未包含您的具体类型"——因为它改写检索 query 时没带上已查到的 C4。
即结果取决于模型是否**碰巧记住**前序事实，而非机制保证。

修复：按 `depends_on` 拓扑分层，同层并行、层间串行；
把之前各层的事实经 `prior_facts` 传给下一层。
**关键**：事实走 `prior_facts`、**不拼进 query** ——
query 是检索串，拼进事实会污染检索（实测 best_sim 0.4643→0.3753）。
"""
import asyncio
from types import SimpleNamespace

from app.agents.graph import SupervisorGraph
from app.services.customer_intent import CustomerIntentPlanner, IntentTask
from app.services.customer_model_tools import ModelToolOutcome


class _RecordingLoop:
    """记录每层收到的参数，并返回带事实的假结果。"""

    def __init__(self, calls: list[dict]):
        self._calls = calls

    async def run(self, *, customer_id, conversation_id, query,
                  planned_intents=None, prior_facts=""):
        self._calls.append({
            "planned_intents": list(planned_intents or []),
            "prior_facts": prior_facts,
            "query": query,
        })
        out = ModelToolOutcome()
        for i, intent in enumerate(planned_intents or [], start=1):
            answer = ("您的风险承受能力等级为 **C4**。"
                      if intent == "risk_restriction" else f"{intent} 说明")
            out.results.append({
                "task_id": f"model-tool-{i}", "intent": intent, "status": "succeeded",
                "answer": answer, "data": {}, "error": None, "clarification": None,
            })
            # 带上决策，用于验证"轨迹按层附加"（不区分层时会显示成同一份）
            out.decisions.append({"round": i, "tool": f"tool_{intent}",
                                  "outcome": "executed", "reasoning": f"{intent}"})
        out.stopped_reason = ("no_tool_call" if "risk_restriction" in (planned_intents or [])
                              else "budget_exhausted")
        return out


def _graph(calls: list[dict]) -> SupervisorGraph:
    graph = SupervisorGraph.__new__(SupervisorGraph)
    graph.model_tool_loop_factory = lambda *, max_rounds: _RecordingLoop(calls)
    graph.database = None
    return graph


def _state(query: str):
    plan = CustomerIntentPlanner().plan(query)
    return SimpleNamespace(
        plan={"normalized_query": "", "tasks": [
            {"task_id": t.task_id, "intent": t.intent, "route": t.route,
             "query": t.query, "depends_on": list(t.depends_on),
             "arguments": {}, "execution_mode": t.execution_mode}
            for t in plan.tasks]},
        customer_id="c1", conversation_id="v",
        combined_query=query, user_message=query, task_results=[],
    )


# ---- 分层逻辑 ----

def test_layers_put_fact_before_knowledge():
    """风险事实（risk_restriction）必须排在制度说明（risk_public）之前。"""
    plan = CustomerIntentPlanner().plan("我有没有风险限制")
    layers = SupervisorGraph._task_layers(plan.tasks)
    assert len(layers) >= 2, f"有依赖时应分出多层，实际 {len(layers)}"
    order = {t.intent: i for i, layer in enumerate(layers) for t in layer}
    assert order["risk_restriction"] < order["risk_public"], \
        f"事实层必须先于制度层执行，实际顺序 {order}"


def test_independent_tasks_share_one_layer():
    """无依赖的任务应在同一层（保持并行，不被无谓串行化）。"""
    plan = CustomerIntentPlanner().plan("你们公司的主营业务是什么")
    layers = SupervisorGraph._task_layers(plan.tasks)
    assert len(layers) == 1, f"无依赖应单层，实际 {len(layers)}"


def test_cyclic_dependency_does_not_loop_or_drop_tasks():
    """循环依赖不得死循环，也不得丢 task（宁可退化为并行）。"""
    tasks = [
        IntentTask("t1", "risk_public", "rag_explanation", "q", depends_on=["t2"]),
        IntentTask("t2", "risk_public", "rag_explanation", "q", depends_on=["t1"]),
    ]
    layers = SupervisorGraph._task_layers(tasks)
    assert sum(len(x) for x in layers) == 2, "不得丢弃 task"


# ---- 事实传递（本次修复的核心）----

def test_second_layer_receives_first_layer_facts():
    calls: list[dict] = []
    asyncio.run(_graph(calls).model_tool_node(_state("我有没有风险限制")))
    assert len(calls) == 2, f"应分两层执行，实际 {len(calls)}"
    assert calls[0]["prior_facts"] == "", "第 1 层不应有前序事实"
    assert "C4" in calls[1]["prior_facts"], \
        "第 2 层必须收到第 1 层查到的等级（这正是 C4 用例失败的原因）"
    assert "【前序查询结果" in calls[1]["prior_facts"]
    assert "不要臆造" in calls[1]["prior_facts"], "应禁止臆造，避免编出事实断言"


def test_facts_never_pollute_retrieval_query():
    """事实必须走 prior_facts，**不得拼进 query**（query 是检索串）。

    ISSUE-20260914-DEP-CONTEXT-POLLUTES-RETRIEVAL：把指令/事实拼进检索串
    实测使 best_sim 从 0.4643 降到 0.3753（逼近 reject 阈值）。
    """
    calls: list[dict] = []
    asyncio.run(_graph(calls).model_tool_node(_state("我有没有风险限制")))
    assert len(calls) == 2
    for c in calls:
        assert c["query"] == "我有没有风险限制", \
            f"query 必须保持用户原问句，实际 {c['query']!r}"
        assert "前序查询结果" not in c["query"], "事实不得混入检索串"


def test_single_layer_query_has_no_prior_facts():
    """单层（无依赖）场景不应注入任何前序事实。"""
    calls: list[dict] = []
    asyncio.run(_graph(calls).model_tool_node(_state("你们公司的主营业务是什么")))
    assert len(calls) == 1
    assert calls[0]["prior_facts"] == ""


def test_merged_results_cover_all_layers():
    """各层结果须合并而非覆盖（否则会丢掉第一层的 task）。"""
    calls: list[dict] = []
    patch = asyncio.run(_graph(calls).model_tool_node(_state("我有没有风险限制")))
    intents = [r["intent"] for r in (patch.get("task_results") or [])]
    assert "risk_restriction" in intents and "risk_public" in intents, \
        f"应合并两层的 task 结果，实际 {intents}"


# ---- 分层后引入的三个缺陷（本轮修复，防止回归）----
# 每层是独立的 loop.run()，若不做标记会出现：
#   ① 不同层产生同名 task_id（都叫 model-tool-1），无法区分归属
#   ② 轨迹被合并后附加到**每个** task 上，两 task 显示完全相同的决策
#   ③ 停止原因直接逗号拼接，看不出哪层是哪种原因

def test_layer_results_have_unique_task_ids():
    """① 各层 task_id 必须唯一（否则无法对齐到具体层）。"""
    calls: list[dict] = []
    patch = asyncio.run(_graph(calls).model_tool_node(_state("我有没有风险限制")))
    ids = [r["task_id"] for r in patch["task_results"]]
    assert len(ids) == len(set(ids)), f"task_id 重复（不同层同名），实际 {ids}"
    # 应能看出层归属
    assert any(i.startswith("L1") for i in ids), f"task_id 应带层前缀，实际 {ids}"
    assert any(i.startswith("L2") for i in ids), f"task_id 应带层前缀，实际 {ids}"


def test_stopped_reason_labels_each_layer_without_duplication():
    """③ 停止原因须按层标注，且**不得重复追加**同一条。"""
    calls: list[dict] = []
    patch = asyncio.run(_graph(calls).model_tool_node(_state("我有没有风险限制")))
    reason = patch["model_tool_stopped_reason"]
    parts = [p.strip() for p in reason.split("|")]
    assert len(parts) == len(set(parts)), f"停止原因出现重复条目: {reason}"
    assert all(p.startswith("L") for p in parts), \
        f"每条停止原因都应带层号（如 L1:...），实际 {reason}"


def test_each_task_shows_only_its_own_layer_decisions():
    """② 每个 task 只应显示**本层**决策，而非全部层的合并轨迹。"""
    from app.services.graph_agent_runner import _results_from_state

    calls: list[dict] = []
    graph = _graph(calls)
    state = _state("我有没有风险限制")
    patch = asyncio.run(graph.model_tool_node(state))
    results = _results_from_state({**state.__dict__, **patch})

    traces = []
    for r in results:
        tr = r.data.get("model_tool_trace") or {}
        traces.append([d.get("tool") for d in (tr.get("decisions") or [])])
        assert tr.get("layer") is not None, \
            f"轨迹应带层号，便于分辨归属：{r.task_id}"
    # 两层意图不同 → 决策序列不应完全相同
    assert len(traces) == 2, f"应有 2 个 task，实际 {len(traces)}"
    assert traces[0] != traces[1], \
        f"两个 task 显示了相同的决策序列（说明轨迹被整体附加）: {traces}"


def test_layer_traces_survive_real_langgraph_state():
    """分层轨迹必须能穿过**真实 LangGraph 状态**（而非仅直接调用节点）。

    ISSUE-20260914-AGENT-STATE-SILENTLY-DROPS-UNDECLARED-KEYS

    这是上一版测试漏掉的关键：直接调用 `model_tool_node` 会通过，
    但真实图执行只把 `AgentState` **已声明**的字段并入状态，未声明的键
    **静默丢弃**（不报错、不告警）。实测中 runner 因此拿不到分层轨迹，
    每个 task 都退回整体轨迹，两个 task 显示完全相同的决策。
    故本测试**用真实 StateGraph 跑一遍**，确认键确实存在于最终状态。
    """
    from langgraph.graph import END, START, StateGraph

    from app.schemas.agents import AgentState

    async def node(state: AgentState) -> dict:
        return {"model_tool_layer_traces": [{"layer": 1, "decisions": [{"tool": "t"}]}]}

    async def _run():
        g = StateGraph(AgentState)
        g.add_node("n", node)
        g.add_edge(START, "n")
        g.add_edge("n", END)
        return await g.compile().ainvoke({
            "request_id": "r", "trace_id": "t",
            "conversation_id": "v", "customer_id": "c", "user_message": "q",
        })

    out = asyncio.run(_run())
    assert "model_tool_layer_traces" in out, (
        "该键未在 AgentState 声明 -> LangGraph 会静默丢弃，"
        "导致 runner 读不到分层轨迹（两个 task 显示相同决策）")


def test_node_patch_keys_are_all_declared_in_state():
    """节点返回的每个键都必须在 AgentState 中声明，防止再犯同类静默丢失。"""
    from app.schemas.agents import AgentState

    fields = set(AgentState.model_fields)
    calls: list[dict] = []
    patch = asyncio.run(_graph(calls).model_tool_node(_state("我有没有风险限制")))
    undeclared = [k for k in patch if k not in fields]
    assert not undeclared, (
        f"model_tool_node 返回了未在 AgentState 声明的键 {undeclared}，这些键会被静默丢弃")


# ---- ISSUE-20260914-GRAPH-GENERATION-MISSING-PRIOR-FACTS ----
# 分层事实此前只进了 `model_tool_node` 的**提示层**（prior_facts），
# 而 `generate_node` 对所有 task 一律用 `state.react_query or state.user_message`，
# 于是风险制度 task 只拿到用户原问句、**看不到前一层查到的 C4**，
# 只能讲通用规则，还自认"资料未完全覆盖"（partially_answered）。
# pipeline 同一场景是对的（它把 dependency_context 附到生成 query）——本条即补齐这一环。

def test_layer_facts_are_stored_on_task_results():
    """各层的结果须带 `data.prior_facts`，供生成阶段使用。"""
    calls: list[dict] = []
    patch = asyncio.run(_graph(calls).model_tool_node(_state("我有没有风险限制")))
    by_intent = {r["intent"]: r for r in patch["task_results"]}

    first = by_intent["risk_restriction"]["data"]
    second = by_intent["risk_public"]["data"]
    assert first.get("prior_facts") == "", "第一层无前序事实"
    assert "C4" in (second.get("prior_facts") or ""), \
        "第二层应带上第一层的事实（C4），供生成阶段使用"


def test_generate_node_uses_layer_facts_in_query():
    """`generate_node` 必须把本层事实并入**生成** query。"""
    import types

    from app.agents.graph import SupervisorGraph

    captured: list[tuple[str, str]] = []

    class _Gen:
        async def generate(self, *, query, result, conversation_summary=""):
            captured.append((result.intent, query))
            return types.SimpleNamespace(answer="a", generation_status="ok",
                                         insufficient=False)

    graph = SupervisorGraph.__new__(SupervisorGraph)
    graph.answer_generator = _Gen()
    state = types.SimpleNamespace(
        react_query="", user_message="我有没有风险限制",
        task_results=[{
            "task_id": "L2-model-tool-1", "intent": "risk_public",
            "status": "succeeded", "answer": "可基于客服知识库引用生成回答。",
            "data": {
                "citations": [{"chunk_id": "c", "document_id": "d", "version_id": "v",
                               "content": "制度文本", "score": 0.9,
                               "title_path": "", "page_number": None, "metadata": {}}],
                "evidence_status": "sufficient",
                "prior_facts": "\n\n【前序查询结果（请基于这些事实作答，不要臆造）】\n"
                               "[risk_restriction] 等级为 **C4**。",
            },
            "error": None, "clarification": None,
        }],
    )
    asyncio.run(graph.generate_node(state))
    assert captured, "应有 task 进入生成"
    intent, query = captured[0]
    assert "C4" in query, (
        f"生成 query 必须含前序事实（否则只能讲通用规则，实际 {query!r}）")
    assert "前序查询结果" in query


def test_generate_node_query_unchanged_without_facts():
    """无前序事实时生成 query 保持原样（不影响既有单层场景）。"""
    import types

    from app.agents.graph import SupervisorGraph

    captured: list[str] = []

    class _Gen:
        async def generate(self, *, query, result, conversation_summary=""):
            captured.append(query)
            return types.SimpleNamespace(answer="a", generation_status="ok",
                                         insufficient=False)

    graph = SupervisorGraph.__new__(SupervisorGraph)
    graph.answer_generator = _Gen()
    state = types.SimpleNamespace(
        react_query="", user_message="你们公司叫什么名字",
        task_results=[{
            "task_id": "model-tool-1", "intent": "company_info",
            "status": "succeeded", "answer": "x",
            "data": {"citations": [{"chunk_id": "c", "document_id": "d",
                                    "version_id": "v", "content": "公司简介",
                                    "score": 0.9, "title_path": "",
                                    "page_number": None, "metadata": {}}],
                     "evidence_status": "sufficient"},
            "error": None, "clarification": None,
        }],
    )
    asyncio.run(graph.generate_node(state))
    assert captured and captured[0] == "你们公司叫什么名字", \
        f"无事实时不应改动生成 query，实际 {captured}"
