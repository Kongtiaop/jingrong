# -*- coding: utf-8 -*-
"""只读子代理并行：模型自主拆分/并行决策 + 真实 runner 映射。

覆盖三层：
1. `parse_fanout_decision`（纯函数）：越界 capability 丢弃、空任务降级、depends_on 清洗、
   **模式以 depends_on 为准**（不采信模型自报 mode）。
2. `ReadOnlySubagentRunner`：capability → 只读工具映射；未登记能力拒绝；失败→partial。
3. `run_read_only_fanout`：端到端——模型决策 → 并发上限 → 融合 + 决策元数据回填。
"""
import asyncio
from typing import Any

from app.schemas.subagent import SubagentResult, SubagentTask
from app.services.subagent_executor import READ_ONLY_CAPABILITIES
from app.services.subagent_planner import ReadOnlyFanoutPlanner, parse_fanout_decision
from app.services.subagent_runner import ReadOnlySubagentRunner, capability_to_tool


# ---------- 1. 纯函数：决策解析 ----------

def test_parse_keeps_only_whitelisted_capabilities():
    decision = parse_fanout_decision(
        {"mode": "parallel", "tasks": [
            {"capability": "customer_asset_summary", "query": "资产"},
            {"capability": "create_order", "query": "帮我下单"},  # 写操作：必须丢弃
        ]}, fallback_query="q")
    caps = [t.read_only_capability for t in decision.tasks]
    assert caps == ["customer_asset_summary"]
    assert all(c in READ_ONLY_CAPABILITIES for c in caps)


def test_parse_falls_back_to_single_serial_task_when_no_valid_task():
    decision = parse_fanout_decision(
        {"mode": "parallel", "tasks": [{"capability": "write_money", "query": "转账"}]},
        fallback_query="我的资产")
    assert decision.source == "fallback_serial"
    assert len(decision.tasks) == 1
    assert decision.tasks[0].read_only_capability == "customer_rag_knowledge"
    assert decision.effective_mode == "serial"


def test_parse_drops_out_of_range_and_self_dependency():
    decision = parse_fanout_decision(
        {"mode": "serial", "tasks": [
            {"capability": "customer_asset_summary", "query": "a", "depends_on": [5, 0]},
            {"capability": "customer_order_status", "query": "b"},
        ]}, fallback_query="q")
    # 下标 5 越界、依赖自身 0 都要剔除；且清洗后按下标对齐
    assert decision.depends_on[0] == []
    assert len(decision.depends_on) == len(decision.tasks)


def test_effective_mode_trusts_depends_on_over_self_reported_mode():
    """实测模型会把有依赖的情况误填 parallel；必须以 depends_on 为准。"""
    decision = parse_fanout_decision(
        {"mode": "parallel", "tasks": [
            {"capability": "customer_risk_restriction", "query": "限制"},
            {"capability": "customer_risk_explanation", "query": "解释", "depends_on": [0]},
        ]}, fallback_query="q")
    assert decision.mode == "parallel"          # 模型自报
    assert decision.effective_mode == "serial"  # 实际执行：有依赖 → 串行


def test_parse_caps_task_count():
    decision = parse_fanout_decision(
        {"mode": "parallel", "tasks": [
            {"capability": "customer_asset_summary", "query": f"q{i}"} for i in range(20)
        ]}, fallback_query="q")
    assert len(decision.tasks) <= 4


def test_parse_assigns_unique_task_ids():
    decision = parse_fanout_decision(
        {"mode": "parallel", "tasks": [
            {"capability": "customer_asset_summary", "query": "a"},
            {"capability": "customer_order_status", "query": "b"},
        ]}, fallback_query="q")
    ids = [t.task_id for t in decision.tasks]
    assert len(set(ids)) == len(ids)


def test_parse_unwraps_tool_call_envelope():
    """回归：`request_tool_call` 返回的是 {name, arguments, reasoning} 包装体。

    初版只认 arguments 内层，导致 `plan()` 每次决策都**静默降级为串行单任务**，
    而输出看起来正常（有答案、succeeded），唯一线索是 source=fallback_serial。
    本用例锁死"包装体必须能解析"。
    """
    envelope = {
        "name": "dispatch_read_tasks",
        "reasoning": "",
        "arguments": {"mode": "parallel", "reason": "两路独立", "tasks": [
            {"capability": "customer_asset_summary", "query": "资产"},
            {"capability": "customer_rag_knowledge", "query": "主营业务"},
        ]},
    }
    decision = parse_fanout_decision(envelope, fallback_query="q")
    assert decision.source == "model", "包装体未被剥壳 → 静默降级"
    assert len(decision.tasks) == 2
    assert decision.effective_mode == "parallel"


def test_parse_envelope_and_bare_arguments_agree():
    """两种入参形态必须得到同一结果（去掉隐形契约）。"""
    inner = {"mode": "serial", "reason": "依赖", "tasks": [
        {"capability": "customer_risk_restriction", "query": "限制"},
        {"capability": "customer_risk_explanation", "query": "解释", "depends_on": [0]},
    ]}
    bare = parse_fanout_decision(inner, fallback_query="q")
    wrapped = parse_fanout_decision(
        {"name": "dispatch_read_tasks", "arguments": inner, "reasoning": "x"}, fallback_query="q")
    assert bare.model_dump() == wrapped.model_dump()
    assert bare.effective_mode == "serial"


# ---------- 2. runner：capability → 只读工具 ----------

def _citation(text: str = "主营财富管理", chunk_id: str = "c1") -> dict:
    """与 `KnowledgeCitation` 真实字段对齐（8 个必填字段）。

    初版测试只给了 chunk_id/content 两个字段 → `KnowledgeCitation(**c)` 抛 TypeError
    → 生成被静默跳过、断言失败。**fixture 必须与真实结构一致**，
    否则测的是假路径（同一类坑在这个项目里已出现过多次）。

    `chunk_id` 可指定：引用去重是按 chunk_id 判定的，测试"两条不同引用"时
    必须给不同的 id，否则会被正确地判为同一个 chunk。
    """
    return {
        "chunk_id": chunk_id, "document_id": "d1", "version_id": "v1", "content": text,
        "score": 0.9, "title_path": "产品服务手册", "page_number": 1,
        "metadata": {}, "vector_similarity": 0.8,
    }


class _FakeTools:
    def __init__(self, *, success=True, error=None, answer="工具答案", citations=None):
        self.calls: list[dict] = []
        self._success = success
        self._error = error
        self._answer = answer
        # 知识类工具真实返回含 citations；资产/订单类不含（两者行为不同，必须能分别模拟）
        self._citations = citations if citations is not None else []

    async def execute(self, *, name, customer_id, query, intent=None, entities=None):
        self.calls.append({"name": name, "customer_id": customer_id,
                           "query": query, "intent": intent})

        class _R:
            pass
        r = _R()
        r.success = self._success
        r.data = {"evidence_status": "ok"}
        if self._citations:
            r.data["citations"] = self._citations
        r.answer = self._answer
        r.error = self._error
        return r


def test_all_whitelisted_capabilities_are_mapped():
    """白名单里的每个 capability 都必须有真实映射，否则接线是假的。"""
    for cap in READ_ONLY_CAPABILITIES:
        assert capability_to_tool(cap) is not None, f"{cap} 未映射到只读工具"


def test_runner_maps_capability_and_injects_customer_id():
    tools = _FakeTools()
    runner = ReadOnlySubagentRunner(tools=tools)
    task = SubagentTask(task_id="t1", query="我的资产", read_only_capability="customer_asset_summary",
                        scope_json={"customer_id": "cust-1"})
    res = asyncio.run(runner(task))
    assert res.status == "success"
    assert tools.calls[0]["name"] == "get_my_asset_summary"
    # customer_id 由 scope_json 注入（服务端指定），模型无法覆盖
    assert tools.calls[0]["customer_id"] == "cust-1"
    assert res.data["tool"] == "get_my_asset_summary"
    assert res.data["read_only_capability"] == "customer_asset_summary"


def test_runner_passes_knowledge_intent_per_capability():
    tools = _FakeTools()
    runner = ReadOnlySubagentRunner(tools=tools)
    asyncio.run(runner(SubagentTask(task_id="a", query="q", read_only_capability="customer_rag_knowledge")))
    asyncio.run(runner(SubagentTask(task_id="b", query="q", read_only_capability="customer_risk_explanation")))
    assert [c["intent"] for c in tools.calls] == ["faq", "risk_public"]


def test_runner_rejects_unmapped_capability_without_guessing():
    tools = _FakeTools()
    runner = ReadOnlySubagentRunner(tools=tools)
    res = asyncio.run(runner(SubagentTask(task_id="x", query="q", read_only_capability="totally_made_up")))
    assert res.status == "failed"
    assert res.error == "capability_not_mapped"
    assert tools.calls == []  # 绝不"猜一个工具试试"


def test_runner_maps_tool_failure_to_partial_keeping_summary():
    tools = _FakeTools(success=False, error="fact_not_found", answer="请补充订单号")
    runner = ReadOnlySubagentRunner(tools=tools)
    res = asyncio.run(runner(SubagentTask(task_id="t1", query="q",
                                          read_only_capability="customer_order_status")))
    assert res.status == "partial"
    assert res.error == "fact_not_found"
    assert res.summary == "请补充订单号"  # 澄清文案不能丢


def test_runner_maps_exception_to_failed():
    class _Boom:
        async def execute(self, **kwargs):
            raise RuntimeError("db down")

    res = asyncio.run(ReadOnlySubagentRunner(tools=_Boom())(
        SubagentTask(task_id="t1", query="q", read_only_capability="customer_asset_summary")))
    assert res.status == "failed"
    assert "RuntimeError" in res.error


# ---------- 3. 端到端：模型决策 → 并发上限 → 融合 ----------

class _FakeProvider:
    """按预设返回一次工具调用决策。

    **必须模拟真实 provider 的返回形态**：`request_tool_call` 返回的是
    `{"name", "arguments", "reasoning"}` 包装体，而不是裸的 arguments。
    初版 fake 直接返回裸 dict，于是"包装体没剥壳"这个 bug 逃过了全部测试
    （见 test_parse_unwraps_tool_call_envelope 的说明）——fake 的形状必须与真实一致。
    """

    def __init__(self, decision: dict | None):
        self._decision = decision
        self._client = object()  # 非 None → 视为可用

    async def request_tool_call(self, *, system, user, tools, model=None, **kwargs):
        if self._decision is None:
            return None
        return {"name": "dispatch_read_tasks", "arguments": self._decision, "reasoning": ""}


class _FakeDatabase:
    def __init__(self):
        self.rows: list[Any] = []

    def session_factory(self):
        outer = self

        class _Session:
            def add(self, row):
                outer.rows.append(row)

            async def commit(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        return _Session()


def _service(fanout_decision, *, runner):
    from app.services.customer_agent_execution import CustomerAgentExecutionService

    class _Svc(CustomerAgentExecutionService):
        def __init__(self):
            pass

    svc = _Svc()
    svc.database = _FakeDatabase()
    svc.redis_client = None

    class _Settings:
        # 这些端到端用例测的是「启用」路径；显式置 True，
        # 避免依赖 settings 的默认值（默认 False 是给生产用的保守值）。
        agent_subagent_fanout_enabled = True
    svc.settings = _Settings()
    svc.intent_fallback = None

    planner = ReadOnlyFanoutPlanner(provider=_FakeProvider(fanout_decision), database=svc.database)
    return svc, planner


def test_fanout_parallel_runs_subtasks_concurrently():
    """模型判定 parallel → 子任务必须真并发（用峰值并发数验证，不靠日志）。"""
    running = 0
    peak = 0

    class _SlowTools(_FakeTools):
        async def execute(self, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.03)
            running -= 1
            return await super().execute(**kwargs)

    decision = {"mode": "parallel", "reason": "三个独立查询", "tasks": [
        {"capability": "customer_asset_summary", "query": "资产"},
        {"capability": "customer_order_status", "query": "订单"},
        {"capability": "customer_rag_knowledge", "query": "业务"},
    ]}
    svc, planner = _service(decision, runner=None)
    runner = ReadOnlySubagentRunner(tools=_SlowTools())

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="资产+订单+业务",
            planner=planner, runner=runner)

    res = asyncio.run(go())
    assert peak >= 2, f"并行模式峰值并发应 ≥2，实际 {peak}"
    assert res.fanout_mode == "parallel"
    # **必须校验 source**：若解析层静默降级，mode 仍是 serial 且答案看着正常，
    # 只有 source 会暴露"根本没走模型决策"。
    assert res.fanout_source == "model", f"静默降级：source={res.fanout_source}"
    assert res.overall_status == "succeeded"
    assert len(res.results) == 3


def test_fanout_serial_when_model_declares_dependency():
    running = 0
    peak = 0

    class _SlowTools(_FakeTools):
        async def execute(self, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.03)
            running -= 1
            return await super().execute(**kwargs)

    decision = {"mode": "serial", "reason": "后者依赖前者", "tasks": [
        {"capability": "customer_risk_restriction", "query": "限制"},
        {"capability": "customer_risk_explanation", "query": "解释", "depends_on": [0]},
    ]}
    svc, planner = _service(decision, runner=None)
    runner = ReadOnlySubagentRunner(tools=_SlowTools())

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="先查限制再解释",
            planner=planner, runner=runner)

    res = asyncio.run(go())
    assert peak == 1, f"串行模式峰值并发应为 1，实际 {peak}"
    assert res.fanout_mode == "serial"
    assert res.overall_status == "succeeded"


def test_fanout_degrades_to_serial_when_model_unavailable():
    svc, planner = _service(None, runner=None)  # 模型不调工具
    runner = ReadOnlySubagentRunner(tools=_FakeTools())

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="我的资产",
            planner=planner, runner=runner)

    res = asyncio.run(go())
    assert res.fanout_mode == "serial"
    assert len(res.results) == 1  # 降级为单任务，绝不空转
    assert res.overall_status == "succeeded"


def test_fanout_audits_decision_with_reason():
    decision = {"mode": "parallel", "reason": "两个独立查询", "tasks": [
        {"capability": "customer_asset_summary", "query": "资产"},
        {"capability": "customer_order_status", "query": "订单"},
    ]}
    svc, planner = _service(decision, runner=None)
    runner = ReadOnlySubagentRunner(tools=_FakeTools())

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="资产和订单",
            planner=planner, runner=runner)

    asyncio.run(go())
    actions = [getattr(r, "action", "") for r in svc.database.rows]
    # 既要有决策审计，也要有每个子任务的执行审计
    assert "customer_agent.subagent_fanout" in actions
    assert actions.count("subagent.ran") == 2


def test_fanout_never_exposes_write_capabilities_end_to_end():
    """模型即使返回写操作能力，端到端也必须没有一次工具调用落到它上面。"""
    tools = _FakeTools()
    decision = {"mode": "parallel", "tasks": [
        {"capability": "customer_asset_summary", "query": "资产"},
        {"capability": "create_order", "query": "帮我买"},
        {"capability": "transfer_money", "query": "转账"},
    ]}
    svc, planner = _service(decision, runner=None)
    runner = ReadOnlySubagentRunner(tools=tools)

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="资产并下单",
            planner=planner, runner=runner)

    res = asyncio.run(go())
    assert len(res.results) == 1
    called = {c["name"] for c in tools.calls}
    assert called == {"get_my_asset_summary"}


def test_fanout_customer_id_is_server_injected_not_model_supplied():
    """模型在 task 里塞 customer_id 也必须被服务端注入覆盖（防越权读他人数据）。"""
    tools = _FakeTools()
    decision = {"mode": "parallel", "tasks": [
        {"capability": "customer_asset_summary", "query": "资产", "customer_id": "someone-else"},
    ]}
    svc, planner = _service(decision, runner=None)
    runner = ReadOnlySubagentRunner(tools=tools)

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="real-customer", conversation_id="cv1", query="资产",
            planner=planner, runner=runner)

    asyncio.run(go())
    assert tools.calls[0]["customer_id"] == "real-customer"


# ---------- 4. 依赖顺序 + 前序事实注入 ----------

def test_order_subtasks_by_dependency_sorts_and_remaps_ids():
    from app.services.subagent_planner import order_subtasks_by_dependency
    # 故意乱序：下标2 依赖下标1，下标1 依赖下标0
    tasks = [SubagentTask(task_id=f"s{i}", query="q",
                          read_only_capability="customer_asset_summary") for i in range(3)]
    deps = [[], [0], [1]]
    ordered, deps_by_id = order_subtasks_by_dependency(tasks, deps)
    assert [t.task_id for t in ordered] == ["s0", "s1", "s2"]
    assert deps_by_id["s1"] == ["s0"]
    assert deps_by_id["s2"] == ["s1"]


def test_order_subtasks_handles_cycle_without_dropping_tasks():
    from app.services.subagent_planner import order_subtasks_by_dependency
    tasks = [SubagentTask(task_id=f"s{i}", query="q",
                          read_only_capability="customer_asset_summary") for i in range(2)]
    ordered, _ = order_subtasks_by_dependency(tasks, [[1], [0]])  # 互相依赖
    assert len(ordered) == 2  # 不丢任务


def test_serial_dependency_order_is_respected_and_facts_flow_forward():
    """回归：executor 只按 Semaphore 限流、不保证顺序。

    必须验证「后序子任务确实拿到了前序结论」，否则依赖修复是假的。
    """
    seen: list[tuple[str, str]] = []

    class _RecordingRunner:
        async def __call__(self, task, prior_facts=""):
            seen.append((task.task_id, prior_facts))
            return SubagentResult(task_id=task.task_id, status="success",
                                  summary=f"答案-{task.task_id}", confidence=0.9)

    # 模型给的是**倒序**的 tasks（后序在前），depends_on 指向原始下标
    decision = {"mode": "serial", "tasks": [
        {"capability": "customer_risk_explanation", "query": "解释", "depends_on": [1]},
        {"capability": "customer_risk_restriction", "query": "限制"},
    ]}
    svc, planner = _service(decision, runner=None)

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="先查限制再解释",
            planner=planner, runner=_RecordingRunner())

    res = asyncio.run(go())
    # 依赖任务必须**先**执行（尽管它在模型给的列表中排第二）
    assert [t for t, _ in seen][0] == "sub-2", f"执行顺序未按依赖排：{seen}"
    # 后序任务必须拿到前序结论
    second_prior = seen[1][1]
    assert "答案-sub-2" in second_prior, f"前序事实未注入：{second_prior!r}"
    assert res.overall_status == "succeeded"


def test_parallel_mode_does_not_inject_prior_facts():
    """并行子任务按定义互不依赖 —— 注入"别人的结果"会引入非确定性。"""
    seen: list[str] = []

    class _RecordingRunner:
        async def __call__(self, task, prior_facts=""):
            seen.append(prior_facts)
            return SubagentResult(task_id=task.task_id, status="success",
                                  summary="x", confidence=0.9)

    decision = {"mode": "parallel", "tasks": [
        {"capability": "customer_asset_summary", "query": "a"},
        {"capability": "customer_order_status", "query": "b"},
    ]}
    svc, planner = _service(decision, runner=None)

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="q",
            planner=planner, runner=_RecordingRunner())

    asyncio.run(go())
    assert all(p == "" for p in seen), f"并行模式不应注入前序事实：{seen}"


# ---------- 5. 知识类子任务必须生成真实答案 ----------

def test_runner_generates_real_answer_for_knowledge_subtask():
    """工具对知识类返回的是**引用+占位串**，必须经 answer_generator 生成真实答案。

    不接生成 → 融合答案里只有"可基于客服知识库引用生成回答。"（状态对、内容空）。
    """
    class _Gen:
        async def generate(self, *, query, result):
            class _A:
                answer = "公司主营业务为财富管理与资产配置。"
            return _A()

    tools = _FakeTools(answer="可基于客服知识库引用生成回答。",
                       citations=[_citation()])
    runner = ReadOnlySubagentRunner(tools=tools, answer_generator=_Gen())
    res = asyncio.run(runner(SubagentTask(
        task_id="t1", query="主营业务", read_only_capability="customer_rag_knowledge")))
    assert res.status == "success"
    assert "财富管理" in res.summary, f"未生成真实答案：{res.summary!r}"


def test_runner_keeps_placeholder_when_no_generator():
    """没注入 generator 时退回原始文案（不崩），但不应声称生成了内容。"""
    tools = _FakeTools(answer="可基于客服知识库引用生成回答。",
                       citations=[_citation()])
    runner = ReadOnlySubagentRunner(tools=tools)
    res = asyncio.run(runner(SubagentTask(
        task_id="t1", query="q", read_only_capability="customer_rag_knowledge")))
    assert res.status == "success"
    assert res.data.get("generation_status") is None


def test_runner_knowledge_generation_failure_does_not_break_subtask():
    class _Boom:
        async def generate(self, **kwargs):
            raise RuntimeError("llm down")

    tools = _FakeTools(answer="占位", citations=[_citation("x")])
    runner = ReadOnlySubagentRunner(tools=tools, answer_generator=_Boom())
    res = asyncio.run(runner(SubagentTask(
        task_id="t1", query="q", read_only_capability="customer_rag_knowledge")))
    assert res.status == "success"  # 生成失败不降级整个子任务
    assert res.summary == "占位"


# ---------- 6. 超时预算必须容得下"检索 + 生成" ----------

def test_default_per_task_timeout_fits_retrieval_plus_generation():
    """回归：初版 per_task_timeout_s=8.0 会把知识类子任务一律砍掉。

    知识类子任务 = 检索(向量+BM25) + 一次生成 LLM 调用，实测 ~11.5s。
    8.0s 下结果为 `subagent_timeout` 且摘要为空 —— 看着像检索故障，
    实际是超时阈值与真实工作量不匹配。这里锁死"默认值必须 ≥ 15s"。
    """
    from app.services.subagent_executor import SubagentExecutor
    assert SubagentExecutor.DEFAULT_PER_TASK_TIMEOUT_S >= 15.0


def test_global_timeout_is_not_shorter_than_per_task_timeout():
    """全局超时必须 ≥ 单任务超时，否则串行时最后一个子任务必被砍。"""
    from app.services.subagent_executor import SubagentExecutor
    assert SubagentExecutor.DEFAULT_GLOBAL_TIMEOUT_S >= SubagentExecutor.DEFAULT_PER_TASK_TIMEOUT_S


def test_executor_timeouts_are_overridable():
    from app.services.subagent_executor import SubagentExecutor
    ex = SubagentExecutor(per_task_timeout_s=1.0, global_timeout_s=2.0)
    assert ex.per_task_timeout_s == 1.0
    assert ex.global_timeout_s == 2.0


# ---------- 7. 开关必须真的控制行为 ----------

def test_disabled_switch_skips_model_decision_entirely():
    """回归：加了一个没人读的开关 = 假开关。

    关闭时必须**不调用模型决策**，直接串行单任务（等价接线前行为）。
    用"provider 是否被调用过"验证，而不是只看输出（输出在两种情况下可能相同）。
    """
    calls: list[str] = []

    class _CountingProvider:
        _client = object()

        async def request_tool_call(self, **kwargs):
            calls.append("called")
            return {"name": "dispatch_read_tasks",
                    "arguments": {"mode": "parallel", "tasks": [
                        {"capability": "customer_asset_summary", "query": "a"},
                        {"capability": "customer_order_status", "query": "b"}]}}

    svc, planner = _service(
        {"mode": "parallel", "tasks": [
            {"capability": "customer_asset_summary", "query": "a"},
            {"capability": "customer_order_status", "query": "b"}]}, runner=None)
    planner.provider = _CountingProvider()  # 换成会记录调用的 provider
    runner = ReadOnlySubagentRunner(tools=_FakeTools())

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="q",
            planner=planner, runner=runner, enabled=False)

    res = asyncio.run(go())
    assert calls == [], f"开关关闭时不应调用模型决策，实际调用 {len(calls)} 次"
    assert res.fanout_source == "disabled"
    assert len(res.results) == 1


def test_enabled_switch_does_call_model_decision():
    calls: list[str] = []

    class _CountingProvider:
        _client = object()

        async def request_tool_call(self, **kwargs):
            calls.append("called")
            return {"name": "dispatch_read_tasks",
                    "arguments": {"mode": "parallel", "tasks": [
                        {"capability": "customer_asset_summary", "query": "a"},
                        {"capability": "customer_order_status", "query": "b"}]}}

    svc, planner = _service(None, runner=None)
    planner.provider = _CountingProvider()
    runner = ReadOnlySubagentRunner(tools=_FakeTools())

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="q",
            planner=planner, runner=runner, enabled=True)

    res = asyncio.run(go())
    assert calls == ["called"]
    assert res.fanout_source == "model"


def test_serial_with_default_runner_still_gets_prior_facts():
    """回归：runner=None + 串行时，包装不能被跳过。

    初版只在 `runner is not None` 时包装，而 `run_parallel_read_only` 内部
    又会自建一个**未包装**的 runner —— 两个缺省拼起来缺一块，
    依赖上下文**静默丢失**（输出看着正常，只是后序答案没用到前序事实）。
    这里用"默认 runner 是否被包装"来验证：记录每次调用的 prior_facts。
    """
    seen_prior: list[str] = []
    seen_tasks: list[str] = []

    class _SpySvc:
        """只替换默认 runner，其余走真实 svc 逻辑。"""

    svc, planner = _service({"mode": "serial", "tasks": [
        {"capability": "customer_risk_restriction", "query": "限制"},
        {"capability": "customer_risk_explanation", "query": "解释", "depends_on": [0]},
    ]}, runner=None)

    class _RecordingRunner:
        async def __call__(self, task, prior_facts=""):
            seen_tasks.append(task.task_id)
            seen_prior.append(prior_facts)
            return SubagentResult(task_id=task.task_id, status="success",
                                  summary=f"答-{task.task_id}", confidence=0.9)

    # 关键：**不传 runner**，让 svc 走 _default_read_only_runner 路径
    svc._default_read_only_runner = lambda: _RecordingRunner()

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="先查再解释",
            planner=planner, enabled=True)

    res = asyncio.run(go())
    assert res.overall_status == "succeeded"
    # 后序子任务必须拿到前序结论 —— 证明默认 runner 也被包装了
    assert len(seen_prior) == 2
    assert "答-sub-1" in seen_prior[1], f"默认 runner 未被包装，前序事实丢失：{seen_prior}"


# ---------- 8. 去重：同一份数据/知识不得被重复读、重复呈现 ----------

def test_merge_dedups_identical_summaries():
    """回归：两个子代理给出相同内容时，融合答案必须只出现一次。

    旧实现无条件拼接 → `[sub-1] 公司主营财富管理…\\n[sub-2] 公司主营财富管理…`。
    与 ISSUE-20260914-SUMMARIZE-DUPLICATE-FILLER 同族（该模块已犯过一次）。
    """
    from app.services.subagent_executor import SubagentExecutor
    results = [
        SubagentResult(task_id="sub-1", status="success", confidence=0.9,
                       summary="公司主营财富管理（引用1）。"),
        SubagentResult(task_id="sub-2", status="success", confidence=0.9,
                       summary="公司主营财富管理（引用1）。"),
    ]
    merged = SubagentExecutor._merge(results)
    assert merged.answer.count("公司主营财富管理") == 1, f"重复：{merged.answer!r}"
    assert merged.dedup["dropped_duplicate_summaries"] == 1


def test_merge_dedups_reworded_same_content():
    """措辞不同但内容相同（引用标注/标点/空白差异）也应去重。

    实测：同一份知识由不同子代理生成时措辞常不同，纯字符串相等去不掉。
    """
    from app.services.subagent_executor import SubagentExecutor
    results = [
        SubagentResult(task_id="sub-1", status="success", confidence=0.9,
                       summary="公司主营财富管理。"),
        SubagentResult(task_id="sub-2", status="success", confidence=0.9,
                       summary="公司主营财富管理（引用 2）"),
    ]
    merged = SubagentExecutor._merge(results)
    assert merged.answer.count("公司主营财富管理") == 1, f"未去重：{merged.answer!r}"


def test_merge_keeps_distinct_content():
    """去重不能误伤**内容不同**的答案。"""
    from app.services.subagent_executor import SubagentExecutor
    results = [
        SubagentResult(task_id="sub-1", status="success", confidence=0.9,
                       summary="您的总资产为 3,000,000.00 元。"),
        SubagentResult(task_id="sub-2", status="success", confidence=0.9,
                       summary="您的订单 O123 已成交。"),
    ]
    merged = SubagentExecutor._merge(results)
    assert "总资产" in merged.answer and "订单" in merged.answer


def test_merge_does_not_conflate_risk_levels():
    """**关键**：去重归一化绝不能把 R1 与 R2 视为同一条。

    `_content_key` 去掉标点/空白，但**必须保留数字与字母**——
    否则"可购买 R1"与"可购买 R2"会被合并，属于用去重引入正确性缺陷。
    """
    from app.services.subagent_executor import SubagentExecutor
    results = [
        SubagentResult(task_id="sub-1", status="success", confidence=0.9,
                       summary="C4 客户可购买 R1 产品。"),
        SubagentResult(task_id="sub-2", status="success", confidence=0.9,
                       summary="C4 客户可购买 R2 产品。"),
    ]
    merged = SubagentExecutor._merge(results)
    assert "R1" in merged.answer and "R2" in merged.answer, f"等级被误合并：{merged.answer!r}"


def test_merge_keeps_actionable_hint_from_failed_subtask():
    """回归：失败方的**可操作提示**不能被丢弃。

    旧实现只拼接 success 的 summary → `fact_not_found` 的"请补充订单号"整个丢失，
    用户只看到"（部分子任务未完成）"，**不知道要补充什么**。
    """
    from app.services.subagent_executor import SubagentExecutor
    results = [
        SubagentResult(task_id="sub-1", status="success", confidence=0.9,
                       summary="您的总资产为 3,000,000.00 元。"),
        SubagentResult(task_id="sub-2", status="partial", error="fact_not_found",
                       summary="请补充更明确的查询对象或订单/产品信息。"),
    ]
    merged = SubagentExecutor._merge(results)
    assert "请补充" in merged.answer, f"可操作提示被丢弃：{merged.answer!r}"
    assert "总资产" in merged.answer, "成功内容不应丢失"
    assert merged.overall_status == "partial"


def test_merge_all_failed_shows_actionable_hint_not_vague_note():
    """全部失败时也要给出可操作提示，而不是只说"部分子任务未完成"。"""
    from app.services.subagent_executor import SubagentExecutor
    results = [
        SubagentResult(task_id="sub-1", status="failed", error="subagent_timeout"),
        SubagentResult(task_id="sub-2", status="partial", error="fact_not_found",
                       summary="请补充更明确的订单号。"),
    ]
    merged = SubagentExecutor._merge(results)
    assert "请补充" in merged.answer
    assert merged.overall_status == "partial"


def test_merge_technical_errors_do_not_leak_into_answer():
    """纯技术错误（超时/能力未映射）不应出现在用户可见文案里。"""
    from app.services.subagent_executor import SubagentExecutor
    results = [
        SubagentResult(task_id="sub-1", status="failed", error="subagent_timeout"),
        SubagentResult(task_id="sub-2", status="failed", error="capability_not_mapped"),
    ]
    merged = SubagentExecutor._merge(results)
    assert "subagent_timeout" not in merged.answer
    assert "capability_not_mapped" not in merged.answer
    assert merged.answer.strip()  # 仍要给出概括性说明


def test_merge_reports_citation_duplication():
    """引用级重复必须被统计出来（实测三个近义 query 重复率 47%）。"""
    from app.services.subagent_executor import SubagentExecutor
    shared = [_citation("公司主营财富管理", "k1"), _citation("持有基金销售牌照", "k2")]
    results = [
        SubagentResult(task_id="sub-1", status="success", confidence=0.9,
                       summary="答案一", data={"citations": shared}),
        SubagentResult(task_id="sub-2", status="success", confidence=0.9,
                       summary="答案二", data={"citations": shared}),
    ]
    merged = SubagentExecutor._merge(results)
    assert merged.dedup["unique_citations"] == 2
    assert merged.dedup["duplicate_citation_refs"] == 2


# ---------- 9. 同一份数据只读一次，但不同知识不可折叠 ----------

def test_parse_folds_duplicate_fact_capabilities():
    """同一事实 capability 出现两次 → 折叠成一个（同一份数据只读一次）。

    实测：模型把"我的总资产"与"我的持仓数量"都映射到 customer_asset_summary，
    导致 get_my_asset_summary 被调 2 次、答案里总资产出现 2 次。
    """
    d = parse_fanout_decision({"mode": "parallel", "tasks": [
        {"capability": "customer_asset_summary", "query": "我的总资产"},
        {"capability": "customer_asset_summary", "query": "我的持仓数量"},
    ]}, fallback_query="q")
    assert len(d.tasks) == 1, f"同一事实能力未折叠：{[t.read_only_capability for t in d.tasks]}"


def test_parse_does_not_fold_knowledge_capabilities():
    """**关键**：知识类 capability 不可折叠 —— 不同 query 检索的是不同知识。

    笼统按 capability 去重会把"公司业务"与"开户流程"合并成一个，**丢答案**。
    这比重复更糟：属于用去重引入正确性缺陷。
    """
    d = parse_fanout_decision({"mode": "parallel", "tasks": [
        {"capability": "customer_rag_knowledge", "query": "公司主营业务"},
        {"capability": "customer_rag_knowledge", "query": "开户流程"},
    ]}, fallback_query="q")
    assert len(d.tasks) == 2, "知识类子任务被误折叠 → 会丢答案"
    assert {t.query for t in d.tasks} == {"公司主营业务", "开户流程"}


def test_folding_remaps_depends_on_correctly():
    """折叠会让下标前移，depends_on 必须重映射，否则指向错位任务。"""
    d = parse_fanout_decision({"mode": "serial", "tasks": [
        {"capability": "customer_asset_summary", "query": "资产"},
        {"capability": "customer_asset_summary", "query": "持仓"},   # 折叠 → 并入下标0
        {"capability": "customer_rag_knowledge", "query": "解释", "depends_on": [0, 1]},
    ]}, fallback_query="q")
    assert len(d.tasks) == 2
    assert d.depends_on[1] == [0], f"依赖未重映射：{d.depends_on}"
    assert len(d.depends_on) == len(d.tasks)


def test_end_to_end_folded_fact_reads_data_once():
    """端到端：同一份数据只读一次。"""
    tools = _FakeTools()
    decision = {"mode": "parallel", "tasks": [
        {"capability": "customer_asset_summary", "query": "我的总资产"},
        {"capability": "customer_asset_summary", "query": "我的持仓数量"},
    ]}
    svc, planner = _service(decision, runner=None)
    runner = ReadOnlySubagentRunner(tools=tools)

    async def go():
        return await svc.run_read_only_fanout(
            customer_id="c1", conversation_id="cv1", query="资产和持仓",
            planner=planner, runner=runner, enabled=True)

    res = asyncio.run(go())
    assert len(tools.calls) == 1, f"同一份数据被读了 {len(tools.calls)} 次"
    assert tools.calls[0]["name"] == "get_my_asset_summary"
    assert res.answer.count("工具答案") == 1, f"答案重复：{res.answer!r}"

