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
        for intent in (planned_intents or []):
            answer = ("您的风险承受能力等级为 **C4**。"
                      if intent == "risk_restriction" else f"{intent} 说明")
            out.results.append({
                "task_id": f"t-{intent}", "intent": intent, "status": "succeeded",
                "answer": answer, "data": {}, "error": None, "clarification": None,
            })
        out.stopped_reason = "no_tool_call"
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
