from __future__ import annotations

from app.models.audit import AuditLog
from app.models.customer_service import CustomerServiceSession
from app.models.outbox import OutboxEvent
from uuid import uuid4
import json
from app.schemas.customer_service import CustomerTaskExecutionResponse, TaskExecutionResultResponse
from app.services.customer_intent import CustomerIntentPlanner, IntentPlan, IntentTask, execution_mode_for_intent, extract_preference_statement
from app.services.customer_rag_handoff import CustomerRagHandoffService
from app.services.customer_task_executor import CustomerTaskExecutor
from app.services.memory_service import CustomerMemoryService
from app.services.shared_memory_service import SharedMemoryService
from app.core.settings import get_settings
from app.infrastructure.qwen import QwenProvider
from app.services.llm_intent_fallback import LLMIntentFallbackService
from app.services.intent_policy_resolver import IntentPolicyResolver
from app.services.bad_case_collector import misroute_collecting_enabled
from app.services.subagent_executor import SubagentExecutor
from app.schemas.subagent import SubagentMergeResult, SubagentResult, SubagentTask
from app.schemas.intent import StructuredEntity, StructuredIntent
import asyncio
import re


AGENT_ROUTE_BY_INTENT = {
    "product_recommendation": "customer_recommendation",
    "risk_restriction": "customer_risk",
    "asset_summary": "analysis",
    "business_operation": "customer_trade_draft",
    "human_handoff": "customer_service",
    "workorder_status": "customer_service",
    "order_status": "customer_service",
    "product_status": "customer_service",
    "company_info": "customer_service",
    "company_business": "customer_service",
    "faq": "customer_service",
    "workflow": "customer_service",
    "policy": "customer_service",
    "risk_public": "customer_service",
    "greeting": "customer_service",
}
AGENTS_BY_ROUTE = {
    "customer_service": ["CustomerServiceAgent"],
    "customer_recommendation": ["CustomerRecommendationCapability"],
    "customer_trade_draft": ["CustomerTradeDraftCapability"],
    "customer_risk": ["CustomerRiskExplanationCapability"],
    "advisor_risk": ["AdvisorAgent", "RiskAgent"],
    "risk": ["RiskAgent"],
    "analysis": ["DataAnalysisAgent"],
    "operation_risk": ["BusinessAgent", "RiskAgent"],
}
ROUTE_PRIORITY = {"operation_risk": 8, "customer_trade_draft": 7, "advisor_risk": 6, "customer_recommendation": 5, "risk": 4, "customer_risk": 4, "analysis": 3, "customer_service": 1}


def route_for_plan(plan) -> tuple[str, list[str]]:
    routes = [AGENT_ROUTE_BY_INTENT.get(task.intent, "customer_service") for task in plan.tasks]
    route = max(routes or ["customer_service"], key=lambda item: ROUTE_PRIORITY[item])
    return route, AGENTS_BY_ROUTE[route]


def _active_product_from_short_term(short_term: dict) -> str | None:
    """从短时记忆里取"当前对话活跃产品"，供指代消解。"""
    ap = short_term.get("active_product")
    if isinstance(ap, str) and ap.strip():
        return ap.strip()
    for res in reversed(short_term.get("task_results") or []):
        if not isinstance(res, dict):
            continue
        data = res.get("data") or {}
        if not isinstance(data, dict):
            continue
        rec = data.get("recommendation") or {}
        matches = rec.get("matches") or []
        if matches and isinstance(matches[-1], dict) and matches[-1].get("name"):
            return str(matches[-1]["name"])
        for key in ("product_name", "name"):
            if data.get(key):
                return str(data[key])
    return None


def _active_product_from_results(results) -> str | None:
    """从本轮执行结果里取活跃产品（供持久化到短时记忆）。"""
    for res in reversed(results):
        data = res.data or {}
        rec = data.get("recommendation") or {}
        matches = rec.get("matches") or []
        if matches and isinstance(matches[-1], dict) and matches[-1].get("name"):
            return str(matches[-1]["name"])
        for key in ("product_name", "name"):
            if data.get(key):
                return str(data[key])
    return None


# 长时记忆可读 key 白名单：只把低风险、非敏感的事实注入上下文/LLM
LONG_TERM_READ_KEYS = {"active_product", "risk_preference", "product_preference", "investment_horizon_preference"}


def long_term_facts(rows) -> dict:
    """从长时记忆行里抽白名单内的低风险事实。"""
    facts: dict[str, str] = {}
    for row in rows:
        if getattr(row, "key", None) in LONG_TERM_READ_KEYS and getattr(row, "value", ""):
            facts[row.key] = str(row.value)
    return facts


_PURE_PREF_REQUEST_MARKERS = re.compile(
    r"(推荐|申购|购买|买入|下单|适合|想要买|给我推|帮我推|推荐什么|有哪些产品|看看产品|筛选|比较|搭配|组合)"
)


def pure_preference_statement(query: str, pref) -> bool:
    """仅当消息基本只是自述偏好、没有要求推荐/购买/筛选时返回 True。"""
    if not pref:
        return False
    q = (query or "").strip()
    if _PURE_PREF_REQUEST_MARKERS.search(q):
        return False
    return True


class CustomerAgentExecutionService:
    _fallback_semaphores: dict[int, asyncio.Semaphore] = {}

    def __init__(self, database, redis_client, *, intent_fallback=None, settings=None) -> None:
        self.database = database
        self.redis_client = redis_client
        self.settings = settings or get_settings()
        self.intent_fallback = intent_fallback

    def _fallback_semaphore(self) -> asyncio.Semaphore:
        limit = max(1, int(self.settings.llm_intent_fallback_max_concurrency))
        semaphore = self._fallback_semaphores.get(limit)
        if semaphore is None:
            semaphore = asyncio.Semaphore(limit)
            self._fallback_semaphores[limit] = semaphore
        return semaphore

    @staticmethod
    def _structured_from_rule_plan(plan) -> StructuredIntent:
        """规则 plan → 结构化意图。

        primary_intent 保留规划器的**原始意图**（asset_summary / workorder_status / ...），
        不只放模板。模板是粗粒度桶（16 意图压成 4），一旦只带模板往下走，意图信息就在
        这一步丢失，后续无论如何保护都无法恢复（历史 bug：asset_summary → T1 → 被降级成 faq）。
        """
        task = plan.tasks[0] if plan.tasks else None
        template = "T0_UNKNOWN"
        if task:
            mapping = {"product_recommendation": "T2_PRODUCT_RECOMMENDATION", "business_operation": "T4_PURCHASE_DRAFT", "human_handoff": "T7_HUMAN_HANDOFF"}
            template = mapping.get(task.intent, "T1_INFORMATION_QUERY")
        return StructuredIntent(
            template=template,
            primary_intent=task.intent if task else "unknown",
            secondary_intents=[t.intent for t in plan.tasks[1:]],
            confidence=task.confidence if task else 0.1,
            requires_clarification=bool(plan.clarification),
            missing_required_fields=[],
            source="rule",
        )

    def _fallback_allowlist(self) -> set[str]:
        return {value.strip() for value in self.settings.llm_intent_fallback_allowlist.split(",") if value.strip()}

    def _intent_model_enabled(self) -> bool:
        """Return whether the model may affect low-risk routing.

        Shadow mode observes only. The execution switch remains separate so
        transaction/draft paths cannot be enabled accidentally by observability
        configuration.
        """
        return bool(self.settings.llm_intent_fallback_enabled)

    # 可查真实业务数据的事实类意图：route=="business_fact"。
    # 这些意图由确定性 planner 从明确业务词（订单/工单/资产/持仓/风险限制/在售）识别，
    # 背后有 CustomerReadTools 的真实查询。绝不能被 T1 信息查询兜底降级成 FAQ ——
    # 否则"查一下我的持仓"会答"资料不足，建议转人工"，而数据就在库里。
    BUSINESS_FACT_INTENTS = frozenset({
        "workorder_status", "order_status", "risk_restriction",
        "asset_summary", "product_status",
    })

    @classmethod
    def _is_business_fact_plan(cls, plan) -> bool:
        """plan 是否全部由事实类意图构成（决定能否被信息查询兜底覆盖）。"""
        tasks = getattr(plan, "tasks", None) or []
        return bool(tasks) and all(t.intent in cls.BUSINESS_FACT_INTENTS for t in tasks)

    def _apply_structured_intent(self, plan, intent: StructuredIntent):
        """Allow only explicitly approved, read/plan-only fallback changes.

        Transaction and draft templates never come from the model: their route remains
        deterministic and later submission still requires verified customer confirmation.

        关键约束（修复历史降级 bug）：
        1. source=="rule" 表示 resolver 让规则胜出（高置信/受保护），此时**原样返回 plan**，
           绝不用模板重建——模板是有损的，重建会把 asset_summary 之类压成 faq。
        2. business_fact 路由的意图（资产/订单/工单/风险限制/产品状态）属于"可查真实数据的
           事实类"查询，不允许被 T1_INFORMATION_QUERY 兜底降级成 FAQ；否则用户问"我的持仓"
           会得到"资料不足，建议转人工"，而数据就在库里。
        """
        if intent.template not in self._fallback_allowlist() or intent.requires_clarification:
            return plan
        # 规则已胜出（resolver 高置信保护或约束合并）→ 不改动 plan
        if intent.source == "rule":
            return plan
        # 事实类意图不允许被信息查询兜底覆盖
        if intent.template == "T1_INFORMATION_QUERY" and self._is_business_fact_plan(plan):
            return plan
        query = plan.tasks[0].query if plan.tasks else plan.normalized_query
        if intent.template == "T2_PRODUCT_RECOMMENDATION":
            task = IntentTask("t1", "product_recommendation", "suitability", query, confidence=intent.confidence, arguments={}, execution_mode=execution_mode_for_intent("product_recommendation"))
            return IntentPlan(normalized_query=plan.normalized_query, tasks=[task])
        if intent.template == "T7_HUMAN_HANDOFF":
            task = IntentTask("t1", "human_handoff", "handoff", query, priority="high", confidence=intent.confidence, arguments={}, execution_mode=execution_mode_for_intent("human_handoff"))
            return IntentPlan(normalized_query=plan.normalized_query, tasks=[task], requires_handoff=True)
        if intent.template == "T1_INFORMATION_QUERY":
            # Only map to public-customer RAG. Do not infer an account/order/risk fact from a model label.
            public_intents = {"company_info", "company_business", "faq", "workflow", "policy", "risk_public"}
            # 多意图保全：规则已识别出多个只读知识 task（如"公司业务"同时命中 company_info +
            # company_business）时，不得因模板兜底把 plan 收敛成单 task —— 那会让用户少拿一路答案
            # （历史现象：管线 task_intents=['company_info']，图=['company_info','company_business']）。
            # 仅当规则 task 全部属于 public_intents（即本次兜底不改变路由性质）时，原样保留 plan。
            rule_tasks = getattr(plan, "tasks", None) or []
            if len(rule_tasks) > 1 and all(t.intent in public_intents for t in rule_tasks):
                return plan
            requested = str(intent.primary_intent or "").strip()
            safe_intent = requested if requested in public_intents else "faq"
            task = IntentTask("t1", safe_intent, "rag_explanation", query, confidence=intent.confidence, arguments={}, execution_mode=execution_mode_for_intent(safe_intent))
            return IntentPlan(normalized_query=plan.normalized_query, tasks=[task])
        return plan

    async def _audit_intent_shadow(self, *, customer_id: str, conversation_id: str, query: str, rule_intent: StructuredIntent, llm_intent: StructuredIntent | None, resolved_intent: StructuredIntent | None, error: str | None, execution_changed: bool = False, fallback_decision: str = "not_attempted") -> None:
        detail = json.dumps({
            "conversation_id": conversation_id,
            "query_length": len(query),
            "rule": rule_intent.model_dump(),
            "llm": llm_intent.model_dump() if llm_intent else None,
            "resolved": resolved_intent.model_dump() if resolved_intent else None,
            "error": error,
            "execution_changed": execution_changed,
            "fallback_decision": fallback_decision,
            "verified_customer_confirmation_required": True,
        }, ensure_ascii=False)
        async with self.database.session_factory() as session:
            session.add(AuditLog(id=str(uuid4()), actor_user_id=customer_id, action="customer_agent.intent_shadow_evaluated", resource_type="customer_service_conversation", resource_id=conversation_id, detail=detail))
            await session.commit()

    async def run_parallel_read_only(
        self,
        *,
        customer_id: str,
        conversation_id: str,
        subtasks: list[SubagentTask],
        runner=None,
        max_concurrent: int = 2,
        per_task_timeout_s: float | None = None,
        global_timeout_s: float | None = None,
    ) -> SubagentMergeResult:
        """受控只读子代理并行入口：对一组可并行的只读子任务做真实 fan-out → 并行执行 → 确定性融合。

        只读能力白名单、并发信号量、每子代理/全局超时、失败降级都在 SubagentExecutor 内部强制。
        资金/订单/风控结论/任何写操作绝不进此层（调用方只传只读子任务）。
        runner：缺省用**真实的** `ReadOnlySubagentRunner`（capability → CustomerReadTools）。
        单元测试仍可注入 fake。审计统一写 AuditLog(action="subagent.ran")，不含私有推理链。
        """
        if runner is None:
            # 真实接线：此前 runner 只能由调用方注入，生产路径**从未**提供过，
            # 导致子代理层虽已实现但零调用方（等于没接线）。现在缺省即真实 runner。
            runner = self._default_read_only_runner()

        async def _audit(task: SubagentTask, result: SubagentResult, dur: float) -> None:
            try:
                async with self.database.session_factory() as session:
                    session.add(AuditLog(
                        id=str(uuid4()), actor_user_id=customer_id,
                        action="subagent.ran", resource_type="customer_service_conversation",
                        resource_id=conversation_id,
                        detail=json.dumps({
                            "task_id": task.task_id, "capability": task.read_only_capability,
                            "status": result.status, "error": result.error,
                            "duration_ms": round(dur * 1000), "read_only": True,
                        }, ensure_ascii=False),
                    ))
                    await session.commit()
            except Exception:  # noqa: BLE001 - 审计失败不影响主流程
                pass

        executor = SubagentExecutor(
            runner=runner, max_concurrent=max_concurrent, audit=_audit,
            per_task_timeout_s=per_task_timeout_s or SubagentExecutor.DEFAULT_PER_TASK_TIMEOUT_S,
            global_timeout_s=global_timeout_s or SubagentExecutor.DEFAULT_GLOBAL_TIMEOUT_S,
        )
        return await executor.execute(subtasks)

    def _default_read_only_runner(self):
        """构造真实的只读 runner（capability → 只读工具 + 答案生成）。

        单独成方法的原因：`run_parallel_read_only` 与 `run_read_only_fanout`
        （需要先包装再传入）都要用它。若各自内联一份，很容易只改一处、
        导致另一条路径少了 answer_generator 或依赖包装 —— 而这类缺失是静默的。
        """
        from app.services.customer_tools import CustomerReadTools
        from app.services.subagent_runner import ReadOnlySubagentRunner
        from app.services.customer_rag_answer import CustomerRagAnswerService
        return ReadOnlySubagentRunner(
            tools=CustomerReadTools(self.database),
            # 必须带上 answer_generator：知识类子任务的工具返回是**引用+占位串**，
            # 不接生成会输出"可基于客服知识库引用生成回答。"这种没有内容的答案。
            answer_generator=CustomerRagAnswerService(QwenProvider(self.settings)),
        )

    @staticmethod
    def _wrap_runner_with_prior_facts(runner, decision) -> Any:
        """串行模式下，把前序子任务的结论作为 `prior_facts` 传给后序子任务。

        **为什么必须包一层**：`SubagentExecutor` 只按 `Semaphore(1)` 限流，
        **不保证执行顺序** —— `asyncio.gather` 按传入顺序创建协程，信号量先到先得，
        所以"依赖的前序任务"未必先跑完。若不在此处显式排依赖顺序，
        后序子任务会拿到空上下文，退化成"模型运气好才正确"（正是分层修复前的旧缺陷）。

        做法：按 `depends_on` 拓扑排序，并为每个 task 记录它依赖的 task_id 集合；
        每个子任务执行前，从已完成的结论里取自己依赖的那部分拼成 `prior_facts`。
        """
        from app.services.subagent_planner import order_subtasks_by_dependency

        ordered, deps_of = order_subtasks_by_dependency(
            decision.tasks, decision.depends_on)
        facts: dict[str, str] = {}
        lock = asyncio.Lock()

        async def _wrapped(task: SubagentTask) -> SubagentResult:
            async with lock:
                prior = "\n".join(
                    facts[dep] for dep in deps_of.get(task.task_id, []) if facts.get(dep))
            # 只把**本任务依赖的**前序结论给它，不是所有已完成任务的结论。
            context = ("\n\n【前序查询结果（请基于这些事实作答，不要臆造）】\n" + prior) if prior else ""
            result = await runner(task, context)
            if isinstance(result, SubagentResult) and result.status == "success":
                async with lock:
                    facts[task.task_id] = f"[{result.task_id}] {result.summary}"
            return result

        return _wrapped

    async def run_read_only_fanout(
        self,
        *,
        customer_id: str,
        conversation_id: str,
        query: str,
        planner=None,
        runner=None,
        enabled: bool | None = None,
    ) -> SubagentMergeResult:
        """**模型自主决定是否并行**的只读查询入口（P0-3 接线）。

        流程：
          1. `ReadOnlyFanoutPlanner` 请模型判断：这个问题要拆几个只读子任务？能不能并行？
          2. 按 `decision.effective_mode` 决定并发上限——
             **并行=2（受控并发），串行=1**，串行时不浪费并发额度也不打乱依赖顺序。
          3. `run_parallel_read_only` 执行 + 审计 + 确定性融合。

        `enabled`：是否启用**模型决策**。缺省读 `AGENT_SUBAGENT_FANOUT_ENABLED`。
        关闭时**不调用模型决策**，直接退化为串行单任务（等价于今天的行为）——
        这样开关是真的控制行为，而不是一个没人读的摆设。
        注意：**无论开关与否，本入口都只做只读**。

        边界：本方法**只读**。它不产生产品推荐/资金/风控结论，只把各只读子任务的
        事实汇总。任何写操作不在 capability 白名单内（executor 第二道校验）。
        """
        from app.services.subagent_planner import (
            ReadOnlyFanoutPlanner, order_subtasks_by_dependency, parse_fanout_decision,
        )
        if enabled is None:
            enabled = bool(getattr(self.settings, "agent_subagent_fanout_enabled", False))
        if not enabled:
            # 开关关闭 → 不请模型决策，直接单任务串行（行为与接线前一致）。
            decision = parse_fanout_decision(None, fallback_query=query, method="disabled")
        else:
            if planner is None:
                planner = ReadOnlyFanoutPlanner(
                    provider=QwenProvider(self.settings), database=self.database)
            decision = await planner.plan(
                customer_id=customer_id, conversation_id=conversation_id, query=query)
        # 给每个子任务注入 customer_id（服务端注入，模型无法指定查谁 —— 防越权读他人数据）
        subtasks = [
            task.model_copy(update={
                "scope_json": {**(task.scope_json or {}), "customer_id": customer_id},
                "budget_json": {**(task.budget_json or {}), "mode": decision.effective_mode},
            })
            for task in decision.tasks
        ]
        # 串行（有依赖）时必须**按依赖顺序**下发：executor 用 gather 创建协程，
        # 顺序即提交顺序，而串行只保证"一次一个"、不保证"谁先"。不排序会让
        # 后序子任务拿到空的前序上下文（退化成"碰巧正确"）。
        if decision.effective_mode == "serial" and len(subtasks) > 1:
            subtasks, _ = order_subtasks_by_dependency(subtasks, decision.depends_on)
        max_concurrent = 2 if decision.effective_mode == "parallel" else 1
        # 串行（有依赖）时，把**前序子任务已取得的事实**注入后序子任务的生成提示。
        # 为什么只给串行：并行子任务之间按定义互不依赖，注入"别人的结果"既无意义
        # 也会引入非确定性（谁先跑完谁先进上下文，结果不可复现）。
        # 且事实**只进生成、不进检索**（见 ISSUE-20260914-DEP-CONTEXT-POLLUTES-RETRIEVAL）。
        #
        # 注意 runner 为 None 时必须**先建出默认 runner 再包装**：否则包装被跳过，
        # `run_parallel_read_only` 内部又自建一个未包装的 runner → 依赖上下文**静默丢失**。
        # （这类"两个地方各自缺省、拼起来缺一块"的缺陷很难从输出看出来，故显式建默认值。）
        if decision.effective_mode == "serial" and len(subtasks) > 1:
            if runner is None:
                runner = self._default_read_only_runner()
            runner = self._wrap_runner_with_prior_facts(runner, decision)
        result = await self.run_parallel_read_only(
            customer_id=customer_id, conversation_id=conversation_id,
            subtasks=subtasks, runner=runner, max_concurrent=max_concurrent)
        # 把决策元数据带回给调用方，便于响应/审计里直接读出"这次是否并行、为什么"。
        result.fanout_mode = decision.effective_mode
        result.fanout_reason = decision.reason
        result.fanout_source = decision.source
        return result

    async def execute(self, *, customer_id: str, conversation_id: str, query: str) -> CustomerTaskExecutionResponse:
        planner = CustomerIntentPlanner()
        memory = CustomerMemoryService(self.database, self.redis_client)
        short_term = await memory.load_short_term(customer_id=customer_id, conversation_id=conversation_id)
        active_handoff = short_term.get("handoff") or {}
        if active_handoff.get("customer_service_session_id"):
            async with self.database.session_factory() as gate_session:
                gate = await gate_session.get(CustomerServiceSession, active_handoff["customer_service_session_id"])
            if gate is not None and gate.status in {"waiting_human", "human_active", "paused"}:
                # Human handoff does not disable the unified Agent. The UI routes ordinary chat input to
                # the human-message channel while this session is active; independent read-only Agent
                # requests must still be able to plan and answer through the Supervisor entry point.
                short_term = {**short_term, "active_handoff": {**active_handoff, "customer_service_session_status": gate.status}}
        user_query = query.strip()
        clarification_count = int(short_term.get("clarification_count") or 0)
        pending = short_term.get("pending_intent_plan") or {}
        original_query = short_term.get("original_query") or user_query
        combined_query = f"{original_query} {user_query}".strip() if pending.get("tasks") and user_query != original_query else user_query
        # 偏好表态：用户明确自述偏好时，持久化到长时记忆（低风险、可撤销）
        pref = extract_preference_statement(user_query)
        if pref:
            pref_key, pref_value = pref
            try:
                await memory.remember(customer_id=customer_id, key=pref_key, value=pref_value, memory_type="conversation_fact", source="customer_stated", conversation_id=conversation_id)
            except Exception:  # noqa: BLE001 - 记忆失败不阻塞主流程
                pass
        # 长时记忆（跨会话）：读取白名单内的低风险事实，补充短时记忆的不足
        long_term = await memory.load_long_term(customer_id=customer_id)
        facts = long_term_facts(long_term)
        active_product = _active_product_from_short_term(short_term) or facts.get("active_product")
        # 情景记忆：读取本客户共享的 conversation_summary，作为回答/意图的上下文
        conversation_context = ""
        try:
            shared = await SharedMemoryService(self.database, self.redis_client).recall(customer_id=customer_id, agent_type="customer_service", key="conversation_summary")
            if shared:
                conversation_context = str(shared[0].value)
        except Exception:  # noqa: BLE001 - 上下文读取失败不阻塞
            conversation_context = ""
        # 纯偏好自述（未要求推荐/购买/筛选，且不是正在澄清的补答）：只落记忆并先确认，不直接自动推产品清单
        if not pending.get("tasks") and pure_preference_statement(user_query, pref):
            await memory.save_short_term(customer_id=customer_id, conversation_id=conversation_id, state={**short_term, "conversation_id": conversation_id, "original_query": user_query, "latest_query": user_query, "task_results": [], "plan_execution_modes": [], "clarification": None, "missing_parameter": False, "clarification_count": 0, "pending_intent_plan": {}, "handoff": None, "active_product": active_product or ""})
            return CustomerTaskExecutionResponse(
                conversation_id=conversation_id,
                overall_status="needs_clarification",
                answer=f"好的，我已记下您的偏好：{pref[1]}。需要我按这个偏好为您推荐合适的产品吗？",
                route="customer_recommendation",
                agents=["CustomerRecommendationCapability"],
                results=[],
                clarification=None,
            )
        plan = planner.plan(combined_query, context={"active_product": active_product})
        should_observe = self.settings.llm_intent_shadow_enabled or self.settings.llm_intent_fallback_enabled
        if should_observe:
            provider = None
            try:
                fallback = self.intent_fallback
                provider = None
                if fallback is None:
                    provider = QwenProvider(self.settings)
                    fallback = LLMIntentFallbackService(provider)
                rule_structured = self._structured_from_rule_plan(plan)
                summary = str(short_term.get("conversation_summary") or "")
                if facts:
                    summary = (summary + "\n" if summary else "") + f"用户长期事实: {json.dumps(facts, ensure_ascii=False)}"
                if conversation_context:
                    summary = (summary + "\n" if summary else "") + f"用户近期会话摘要: {conversation_context[:1200]}"
                async with self._fallback_semaphore():
                    llm_structured = await asyncio.wait_for(fallback.classify(query=combined_query, conversation_summary=summary), timeout=self.settings.llm_intent_fallback_timeout_seconds)
                resolved = IntentPolicyResolver().resolve(rule_intent=rule_structured, llm_intent=llm_structured)
                changed = False
                decision = "shadow_only"
                if self.settings.llm_intent_fallback_enabled:
                    fallback_plan = self._apply_structured_intent(plan, resolved)
                    changed = fallback_plan != plan
                    plan = fallback_plan
                    if changed:
                        decision = "allowlisted_plan_applied"
                    elif resolved.template not in self._fallback_allowlist():
                        decision = "template_not_allowlisted"
                    elif resolved.requires_clarification:
                        decision = "requires_clarification"
                    else:
                        decision = "no_safe_mapping"
                if self.settings.llm_intent_shadow_enabled or self.settings.llm_intent_fallback_enabled:
                    await self._audit_intent_shadow(customer_id=customer_id, conversation_id=conversation_id, query=combined_query, rule_intent=rule_structured, llm_intent=llm_structured, resolved_intent=resolved, error=None, execution_changed=changed, fallback_decision=decision)
                # 数据飞轮盲区补漏：规则"自信但方向错"的高置信误判（如资格/推荐问句被路由进交易草稿）。
                # 现有 detect_bad_case 只在 unknown/低置信/规则兜底等弱势信号触发，抓不到这类。
                if misroute_collecting_enabled():
                    try:
                        from app.services.bad_case_collector import BadCaseStore
                        BadCaseStore().collect_misroute(combined_query, rule_intent=rule_structured, llm_intent=llm_structured)
                    except Exception:  # noqa: BLE001 - 采集失败不影响主流程
                        pass
            except Exception as exc:
                if self.settings.llm_intent_shadow_enabled:
                    await self._audit_intent_shadow(customer_id=customer_id, conversation_id=conversation_id, query=combined_query, rule_intent=self._structured_from_rule_plan(plan), llm_intent=None, resolved_intent=None, error=f"{type(exc).__name__}: {exc}", execution_changed=False, fallback_decision="fallback_error")
                # Model fallback is advisory only; deterministic planner remains authoritative on failure.
            finally:
                if provider is not None:
                    await provider.close()
        route, agents = route_for_plan(plan)
        if plan.clarification:
            next_count = clarification_count + 1
            if next_count >= 2:
                handoff = await CustomerRagHandoffService(self.database).handoff(customer_id=customer_id, query=original_query, intent="unknown", evidence_status="clarification_exhausted", citations=[])
                await memory.save_short_term(customer_id=customer_id, conversation_id=conversation_id, state={**short_term, "conversation_id": conversation_id, "original_query": original_query, "latest_query": user_query, "clarification_count": next_count, "pending_intent_plan": {}, "clarification": None, "missing_parameter": False, "handoff": handoff, "active_product": active_product})
                return CustomerTaskExecutionResponse(conversation_id=conversation_id, overall_status="handoff", answer="为避免反复追问，已为您转接人工客服。", route="customer_service", agents=["CustomerServiceAgent"], results=[], clarification=None, requires_handoff=True, work_order_id=handoff["work_order_id"], ticket_no=handoff["ticket_no"], customer_service_session_id=handoff["customer_service_session_id"], customer_service_session_status=handoff["customer_service_session_status"])
            await memory.save_short_term(customer_id=customer_id, conversation_id=conversation_id, state={**short_term, "conversation_id": conversation_id, "original_query": original_query, "latest_query": user_query, "clarification_count": next_count, "pending_intent_plan": {"tasks": [item.__dict__ for item in plan.tasks], "execution_modes": sorted({item.execution_mode for item in plan.tasks})}, "clarification": plan.clarification, "missing_parameter": True, "active_product": active_product})
            return CustomerTaskExecutionResponse(conversation_id=conversation_id, overall_status="needs_clarification", answer=plan.clarification, route=route, agents=agents, results=[], clarification=plan.clarification, requires_handoff=plan.requires_handoff)
        results = await CustomerTaskExecutor(self.database, conversation_summary=conversation_context).execute(customer_id=customer_id, plan=plan)
        overall, clarification, aggregate_answer = CustomerTaskExecutor.summarize(results, requires_handoff=plan.requires_handoff)
        answer_lines = [aggregate_answer] if aggregate_answer else []
        needs_handoff = plan.requires_handoff or any(item.error == "low_confidence" for item in results)
        handoff = None
        if needs_handoff:
            handoff = await CustomerRagHandoffService(self.database).handoff(customer_id=customer_id, query=query.strip(), intent=next((item.intent for item in plan.tasks if item.intent != "human_handoff"), "human_handoff"), evidence_status="low_confidence" if any(item.error == "low_confidence" for item in results) else "customer_requested", citations=[])
            answer_lines.append(f"已为您转接人工客服，工单号：{handoff['ticket_no']}。")
        react_results = [item for item in results if item.data and item.data.get("execution_mode") == "bounded_react"]
        if react_results:
            audit_detail = json.dumps({
                "conversation_id": conversation_id,
                "query_length": len(query.strip()),
                "execution_mode": "bounded_react",
                "task_count": len(react_results),
                "tasks": [
                    {
                        "task_id": item.task_id,
                        "intent": item.intent,
                        "status": item.status,
                        "error": item.error,
                        "tool_call_count": len(item.data.get("tool_calls", [])),
                        "tools": [call.get("tool") for call in item.data.get("tool_calls", [])],
                    }
                    for item in react_results
                ],
                "private_reasoning_logged": False,
                "write_tools_used": False,
            }, ensure_ascii=False)
            async with self.database.session_factory() as audit_session:
                audit_session.add(AuditLog(
                    id=str(uuid4()),
                    actor_user_id=customer_id,
                    action="customer_service.bounded_react_executed",
                    resource_type="customer_service_conversation",
                    resource_id=conversation_id,
                    detail=audit_detail,
                ))
                await audit_session.commit()
        state = {**short_term, "conversation_id": conversation_id, "original_query": short_term.get("original_query") or query.strip(), "latest_query": query.strip(), "task_results": [item.__dict__ for item in results], "plan_execution_modes": sorted({item.execution_mode for item in plan.tasks}), "clarification": clarification, "missing_parameter": bool(clarification), "clarification_count": clarification_count, "pending_intent_plan": {}, "handoff": handoff, "active_product": _active_product_from_results(results) or active_product}
        await memory.save_short_term(customer_id=customer_id, conversation_id=conversation_id, state=state)
        # 长时记忆（跨会话）：把本轮出现的活跃产品持久化，供后续新会话消解"那个产品"
        resolved_product = _active_product_from_results(results) or active_product
        if resolved_product:
            try:
                await memory.remember(customer_id=customer_id, key="active_product", value=resolved_product, memory_type="conversation_fact", source="conversation", conversation_id=conversation_id)
            except Exception:  # noqa: BLE001 - 记忆失败不阻塞主流程
                pass
        # 对话中后台异步提炼情景记忆：每轮入队 memory.summary_extract，由 memory_worker 递进提炼落库
        try:
            async with self.database.session_factory() as session:
                session.add(OutboxEvent(
                    id=str(uuid4()), event_type="memory.summary_extract",
                    aggregate_type="customer_memory", aggregate_id=customer_id,  # 合法 UUID；conversation_id 放 payload
                    payload_json={
                        "customer_id": customer_id, "conversation_id": conversation_id,
                        "original_query": original_query, "latest_query": user_query,
                        "task_results": [item.answer for item in results if item.answer][:5],
                        "active_product": active_product or "",
                        "handoff_reason": "low_confidence" if any(item.error == "low_confidence" for item in results) else "",
                    },
                    status="pending",
                ))
                await session.commit()
        except Exception:  # noqa: BLE001 - 摘要入队失败不阻塞主流程
            pass
        return CustomerTaskExecutionResponse(conversation_id=conversation_id, overall_status=overall, answer="\n".join(answer_lines) or "当前没有可返回的结果，建议转人工客服。", route=route, agents=agents, results=[TaskExecutionResultResponse(task_id=item.task_id, intent=item.intent, status=item.status, answer=item.answer, data=item.data or {}, error=item.error, clarification=item.clarification) for item in results], clarification=clarification, requires_handoff=needs_handoff, work_order_id=handoff["work_order_id"] if handoff else None, ticket_no=handoff["ticket_no"] if handoff else None, customer_service_session_id=handoff["customer_service_session_id"] if handoff else None, customer_service_session_status=handoff["customer_service_session_status"] if handoff else None)
