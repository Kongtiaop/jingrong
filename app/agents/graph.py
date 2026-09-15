from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.schemas.agents import AgentState
from app.services.customer_intent import (
    CustomerIntentPlanner,
    IntentPlan,
    IntentTask,
    execution_mode_for_intent,
    extract_preference_statement,
)
from app.services.customer_agent_execution import pure_preference_statement, route_for_plan
from app.services.customer_rag_service import CustomerRagResult
from app.services.customer_task_executor import TOOL_BY_INTENT, CustomerTaskExecutor, TaskExecutionResult
from app.services.knowledge_retrieval import KnowledgeCitation


class PlanProvider(Protocol):
    def plan(self, query: str, context: dict | None = None) -> IntentPlan: ...


class TaskExecutor(Protocol):
    async def execute(self, *, customer_id: str, plan: IntentPlan) -> list[TaskExecutionResult]: ...


class ReadOnlyTool(Protocol):
    """只读知识工具（read-only ReAct 循环每轮调用），与 CustomerReadTools.execute 同签名。"""

    async def execute(self, *, name: str, customer_id: str, query: str, intent: str, entities: dict) -> Any: ...


class AnswerGenerator(Protocol):
    """只读命中引用后的 LLM 答案生成，与 CustomerRagAnswerService.generate 同签名。"""

    async def generate(self, *, query: str, result: CustomerRagResult) -> Any: ...


_REWRITE_POOL = ["请提供官方流程、适用条件和注意事项", "请说明相关制度或流程，并给出依据"]

# 只读知识任务无证据时的兜底文案。取值与管线保持一致：
# - answer 取自 CustomerRagService 的 handoff message（工具返回的 result.answer 已带此串，优先使用它，
#   仅在工具未返回 message 时回退到本常量）；
# - clarification 取自 CustomerTaskExecutor._bounded_react_knowledge 的占位串。
# 目的：同一语义不再出现两种文案，避免 answer 字段产生"假的"路径差异。
_FALLBACK_NO_EVIDENCE = "当前知识库没有足够依据，建议转人工客服或创建工单。"
_FALLBACK_CLARIFICATION = "当前资料不足，可以补充问题或转人工客服。"

# 长时记忆可读白名单（与 customer_agent_execution.LONG_TERM_READ_KEYS 保持一致）
LONG_TERM_READ_KEYS = {"active_product", "risk_preference", "product_preference", "investment_horizon_preference"}


def _rebuild_plan(plan_dict: dict) -> IntentPlan:
    tasks = [
        IntentTask(
            task_id=t["task_id"], intent=t["intent"], route=t["route"], query=t["query"],
            priority=t.get("priority", "normal"), depends_on=list(t.get("depends_on", [])),
            required=t.get("required", True), arguments=t.get("arguments", {}),
            confidence=t.get("confidence", 0.0), status=t.get("status", "planned"),
            execution_mode=t.get("execution_mode", "controlled_workflow"),
        )
        for t in plan_dict.get("tasks", [])
    ]
    return IntentPlan(
        normalized_query=plan_dict.get("normalized_query", ""),
        tasks=tasks,
        clarification=plan_dict.get("clarification"),
        requires_handoff=bool(plan_dict.get("requires_handoff")),
    )


def _plan_to_dict(plan: IntentPlan) -> dict:
    return {
        "normalized_query": plan.normalized_query,
        "tasks": [t.__dict__ for t in plan.tasks],
        "clarification": plan.clarification,
        "requires_handoff": plan.requires_handoff,
    }


def _active_product_from_short_term(short_term: dict) -> str | None:
    """从短时记忆里取"当前对话活跃产品"，供指代消解（与管线同逻辑）。"""
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


class SupervisorGraph:
    """真实编排 LangGraph 图：把确定性编排管线映射成可编译/可运行的状态图。

    节点：intent_plan → route → (澄清条件分支：clarify / handoff / execute
          / 只读 ReAct 回环 react_step→react_done→generate) → summarize → persist → END。

    每个节点复用现有确定性 handler（CustomerIntentPlanner / route_for_plan /
    CustomerTaskExecutor / CustomerTaskExecutor.summarize），故"图"与"确定性管线"
    共享同一套业务逻辑，只是用 LangGraph 显式表达节点、边与条件路由。

    planner / executor / react_tool / answer_generator 可注入（测试传 fake），默认用真实实现。
    database / redis_client 可选注入：不给则图退化为"纯逻辑图"（不读写记忆、不建工单），
    仍可编译运行 —— 现有单测走这条路。给全则 persist/clarify/handoff 节点补齐管线全部副作用。
    """

    def __init__(self, *, planner: PlanProvider | None = None, executor: TaskExecutor | None = None,
                 react_tool: ReadOnlyTool | None = None, answer_generator: AnswerGenerator | None = None,
                 database: Any | None = None, redis_client: Any | None = None,
                 model_tool_loop_factory: Any | None = None,
                 max_iterations: int | None = None, max_tool_calls: int | None = None) -> None:
        self.planner = planner or CustomerIntentPlanner()
        self.executor = executor
        self.react_tool = react_tool
        self.answer_generator = answer_generator
        self.database = database
        self.redis_client = redis_client
        # 模型自主工具调度的构造器：签名 (max_rounds: int) -> CustomerModelToolLoop。
        # 由 runner 注入（内部绑定 provider/tools/database）；缺省为 None → 走确定性 ReAct。
        self.model_tool_loop_factory = model_tool_loop_factory
        # ReAct 预算默认直接取管线的常量，杜绝两条路径再次漂移
        # （历史上图默认 4/8 而管线 20/20，导致双跑对照不公平）。
        self.max_iterations = (max_iterations if max_iterations is not None
                               else CustomerTaskExecutor.MAX_REACT_ITERATIONS)
        self.max_tool_calls = (max_tool_calls if max_tool_calls is not None
                               else CustomerTaskExecutor.MAX_REACT_TOOL_CALLS)

    # ---- 记忆辅助（database 未注入时全部安全降级为空操作） ----
    def _memory(self):
        if self.database is None:
            return None
        from app.services.memory_service import CustomerMemoryService
        return CustomerMemoryService(self.database, self.redis_client)

    @staticmethod
    def _is_readonly_knowledge(plan: IntentPlan) -> bool:
        """只读知识意图（走 LangGraph ReAct 循环）；其余（交易/风控等）走确定性节点。"""
        return bool(plan.tasks) and all(t.intent in CustomerTaskExecutor.REACT_INTENTS for t in plan.tasks)

    def _plan(self, query: str, active_product: str | None) -> IntentPlan:
        """调用 planner，兼容只接受单参数的轻量/测试实现。"""
        try:
            return self.planner.plan(query, context={"active_product": active_product})
        except TypeError:
            return self.planner.plan(query)

    # ---- 节点 ----
    async def intent_plan_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """装载短时/长时/共享记忆，拼澄清补答 query，再规划。

        与管线一致的关键点：
        - 澄清补答时把 original_query + 本轮 user_message 拼接，否则规划器看不到原问题
        - active_product 从短时记忆（或长时事实兜底）取，供指代消解
        """
        memory = self._memory()
        short_term: dict = {}
        if memory is not None and state.conversation_id:
            try:
                short_term = await memory.load_short_term(
                    customer_id=state.customer_id or "", conversation_id=state.conversation_id)
            except Exception:  # noqa: BLE001 - 记忆读取失败不阻塞主流程
                short_term = {}

        clarification_count = int(short_term.get("clarification_count") or state.clarification_count or 0)
        pending = short_term.get("pending_intent_plan") or state.pending_intent_plan or {}
        original_query = short_term.get("original_query") or state.user_message
        combined = (f"{original_query} {state.user_message}".strip()
                    if pending.get("tasks") and state.user_message != original_query
                    else state.user_message)

        facts: dict = {}
        if memory is not None:
            try:
                rows = await memory.load_long_term(customer_id=state.customer_id or "")
                facts = {r.key: str(r.value) for r in rows
                         if getattr(r, "key", None) in LONG_TERM_READ_KEYS and getattr(r, "value", "")}
            except Exception:  # noqa: BLE001
                facts = {}

        conversation_context = state.conversation_context
        if self.database is not None and not conversation_context:
            try:
                from app.services.shared_memory_service import SharedMemoryService
                shared = await SharedMemoryService(self.database, self.redis_client).recall(
                    customer_id=state.customer_id or "", agent_type="customer_service",
                    key="conversation_summary")
                if shared:
                    conversation_context = str(shared[0].value)
            except Exception:  # noqa: BLE001 - 上下文读取失败不阻塞
                conversation_context = ""

        active_product = _active_product_from_short_term(short_term) or facts.get("active_product") or state.active_product

        # ---- 偏好链路（与管线对齐）----
        # 管线在规划**之前**做两件事，图此前缺失（ISSUE-20260914-GRAPH-MISSING-PREFERENCE-CHAIN）：
        # ① 用户自述偏好 → 落长时记忆（低风险、可撤销）
        # ② 纯偏好自述（未要求推荐/购买/筛选）→ 短路：只确认、不自动推产品清单
        preference = extract_preference_statement(state.user_message)
        pref_patch: dict[str, Any] = {}
        if preference and memory is not None and state.is_customer:
            pref_key, pref_value = preference
            try:
                await memory.remember(
                    customer_id=state.customer_id or "", key=pref_key, value=pref_value,
                    memory_type="conversation_fact", source="customer_stated",
                    conversation_id=state.conversation_id)
            except Exception:  # noqa: BLE001 - 记忆写入失败不阻塞主流程
                pass
            # 共享记忆语义层：客服观察写入 shared 作用域，供投顾/推荐等其它 Agent 复用。
            # 与私有写入分开 try：契约写入失败不应连带跳过 shared 观察。
            try:
                from app.services.shared_memory_service import SharedMemoryService
                await SharedMemoryService(self.database, self.redis_client).remember(
                    customer_id=state.customer_id or "", agent_type="customer_service",
                    key=pref_key, value=pref_value, memory_type="observation", scope="shared",
                    source="customer_stated", conversation_id=state.conversation_id,
                    actor_user_id=state.customer_id)
            except Exception:  # noqa: BLE001 - 共享写入失败不阻塞主流程
                pass
            pref_patch["preference_recorded"] = {"key": pref_key, "value": pref_value}

        # 纯偏好短路：仅当不是澄清补答（pending 为空）且为真实客户时才短路，与管线一致。
        # is_customer=False（staff/advisor 代客分析）不得短路——员工的自述不是客户偏好。
        if (state.is_customer and not pending.get("tasks")
                and pure_preference_statement(state.user_message, preference)):
            pref_patch.update({
                "preference_short_circuit": True,
                "plan": {},
                "route": "customer_recommendation",
                "selected_agents": ["CustomerRecommendationCapability"],
                "task_results": [],
                "clarification": None,
                "overall_status": "needs_clarification",
                "final_response": f"好的，我已记下您的偏好：{preference[1]}。需要我按这个偏好为您推荐合适的产品吗？",
                "active_product": active_product or "",
            })
            return pref_patch

        plan = self._plan(combined, active_product)
        return {
            "plan": _plan_to_dict(plan),
            "clarification_count": clarification_count,
            "pending_intent_plan": pending,
            "combined_query": combined,
            "active_product": active_product or "",
            "long_term_facts": facts,
            "conversation_context": conversation_context,
        }

    async def route_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        plan = _rebuild_plan(state.plan)
        route, agents = route_for_plan(plan)
        return {"route": route, "selected_agents": list(agents)}

    async def execute_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        if self.executor is not None:
            results = await self.executor.execute(customer_id=state.customer_id or "", plan=_rebuild_plan(state.plan))
        elif self.database is not None:
            # 与管线一致：注入 conversation_summary，否则生成质量与管线不等价
            executor = CustomerTaskExecutor(self.database, conversation_summary=state.conversation_context)
            results = await executor.execute(customer_id=state.customer_id or "", plan=_rebuild_plan(state.plan))
        else:
            raise RuntimeError("SupervisorGraph 缺少 executor（真实运行需注入 CustomerTaskExecutor）")
        return {"task_results": [r.__dict__ for r in results]}

    async def summarize_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        results = [TaskExecutionResult(**r) for r in state.task_results]
        plan = _rebuild_plan(state.plan)
        overall, clarification, aggregate_answer = CustomerTaskExecutor.summarize(
            results, requires_handoff=plan.requires_handoff)
        needs_handoff = plan.requires_handoff or any(r.error == "low_confidence" for r in results)
        return {
            "overall_status": overall,
            "clarification": clarification,
            "final_response": aggregate_answer or "当前没有可返回的结果，建议转人工客服。",
            "needs_handoff": needs_handoff,
        }

    async def clarify_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        clarification = (state.plan or {}).get("clarification") or "请补充信息。"
        return {
            "overall_status": "needs_clarification",
            "clarification_count": state.clarification_count + 1,
            "clarification": clarification,
            "final_response": clarification,
        }

    async def handoff_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """转人工：建工单/客服会话，写入 state 供 runner 回给前端。"""
        patch: dict[str, Any] = {
            "overall_status": "handoff",
            "clarification_count": state.clarification_count + 1,
            "final_response": "为避免反复追问，已为您转接人工客服。",
            "needs_handoff": True,
        }
        if self.database is not None and state.customer_id:
            try:
                from app.services.customer_rag_handoff import CustomerRagHandoffService
                plan = _rebuild_plan(state.plan)
                intent = next((t.intent for t in plan.tasks if t.intent != "human_handoff"), "human_handoff")
                evidence = "clarification_exhausted" if (state.plan or {}).get("clarification") else "customer_requested"
                handoff = await CustomerRagHandoffService(self.database).handoff(
                    customer_id=state.customer_id, query=state.combined_query or state.user_message,
                    intent=intent, evidence_status=evidence, citations=[])
                patch.update({
                    "work_order_id": handoff.get("work_order_id"),
                    "ticket_no": handoff.get("ticket_no"),
                    "customer_service_session_id": handoff.get("customer_service_session_id"),
                    "customer_service_session_status": handoff.get("customer_service_session_status"),
                })
                if handoff.get("ticket_no"):
                    patch["final_response"] = f"为避免反复追问，已为您转接人工客服，工单号：{handoff['ticket_no']}。"
            except Exception:  # noqa: BLE001 - 转人工失败仍返回话术，不阻塞
                pass
        return patch

    # ---- 模型自主工具调度（只读查询；与确定性 ReAct 二选一） ----
    def _model_tool_selection_enabled(self) -> bool:
        """是否启用模型自主工具选择。未注入 provider/开关关闭时返回 False → 走确定性 ReAct。"""
        if self.model_tool_loop_factory is None or self.database is None:
            return False
        try:
            from app.core.settings import get_settings
            return bool(getattr(get_settings(), "agent_model_tool_selection_enabled", False))
        except Exception:  # noqa: BLE001 - 配置不可读时保守走确定性路径
            return False

    async def model_tool_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """模型自主选择只读工具并循环，直到模型不再调用或预算用尽。

        与 `react_step_node` 的区别：工具名、参数、改写、是否续调**全部由模型决定**，
        而非硬编码 `search_customer_knowledge` + 2 条固定改写模板。
        安全：模型只能从只读白名单里选（写操作进不来），customer_id 由服务端注入。

        **按 `depends_on` 分层执行**（ISSUE-20260914-GRAPH-MODEL-TOOL-IGNORES-DEPENDS-ON）：
        此前把 `plan.tasks` 压成**一串意图名**一次性交给模型循环，导致 `depends_on`
        完全不可见 —— 依赖既不参与调度，前序答案也不会传给后序 task。
        后果实测：`我有没有风险限制`（C4 客户）pipeline 正确答出"可投资 R1-R4"，
        而 graph 因模型改写检索 query 时没带上已查到的 C4，输出"未包含您的具体类型"。
        即结果取决于模型是否**碰巧记住**前序事实，而非机制保证。

        现按依赖分层：同层并行、层间串行，并把已完成层的答案作为**事实上下文**
        传给下一层的查询与生成，使"用事实（客户等级）套规则（制度）"成为必然而非运气。
        """
        if self.model_tool_loop_factory is None:
            raise RuntimeError("model_tool_node 需要注入 model_tool_loop_factory")
        try:
            from app.core.settings import get_settings
            max_rounds = int(getattr(get_settings(), "agent_model_tool_max_rounds", 4))
        except Exception:  # noqa: BLE001
            max_rounds = 4

        plan = _rebuild_plan(state.plan)
        base_query = state.combined_query or state.user_message

        merged_rounds: list[dict] = []
        merged_names: list[str] = []
        merged_results: list[dict] = []
        stop_reasons: list[str] = []
        # 每层的轨迹**按层保存**，不再合并成一个大列表。
        # 为什么（修复本轮引入的缺陷）：
        #   初版把各层 decisions 全部 extend 成一个扁平列表，而
        #   `graph_agent_runner._results_from_state` 会把该轨迹附加到**每个 task** 的
        #   data 上 —— 导致两个 task 显示出**完全相同**的模型决策序列，
        #   排查时无法分辨"哪层做了什么"。实测 C4 用例即出现该现象。
        layer_traces: list[dict] = []

        for layer_index, layer in enumerate(self._task_layers(plan.tasks), start=1):
            planned_intents = [t.intent for t in layer]
            # 把**之前各层**已产出的事实作为上下文（语义与文案与 pipeline
            # `CustomerTaskExecutor._dependency_context` 一致）。
            # **注意必须与 query 分离传入**：query 是检索串，拼进事实会污染检索
            # （实测 best_sim 0.4643→0.3753，见 ISSUE-20260914-DEP-CONTEXT-POLLUTES-RETRIEVAL）。
            dep_context = self._facts_context(merged_results)

            loop = self.model_tool_loop_factory(max_rounds=max_rounds)
            try:
                outcome = await loop.run(
                    customer_id=state.customer_id or "",
                    conversation_id=state.conversation_id,
                    query=base_query,
                    planned_intents=planned_intents,
                    prior_facts=dep_context,
                )
            except Exception as exc:  # noqa: BLE001 - 单层失败不拖垮整体
                logger.warning("model_tool.layer_failed intents=%s err=%s",
                               planned_intents, type(exc).__name__)
                continue

            layer_prefix = f"L{layer_index}"
            merged_rounds.extend(
                {**r.__dict__, "layer": layer_index, "layer_intents": planned_intents}
                for r in outcome.rounds)
            merged_names.extend(outcome.tool_names)
            merged_results.extend(self._tag_layer_results(
                outcome.results, layer_index=layer_index, layer_prefix=layer_prefix,
                layer_intents=planned_intents, prior_facts=dep_context))
            stop_reasons.append(f"{layer_prefix}:{outcome.stopped_reason}")
            # 该层的决策序列单独保存，供 runner 只附加到**本层**的 task 上
            layer_traces.append({
                "layer": layer_index,
                "layer_intents": planned_intents,
                "decisions": list(outcome.decisions),
                "rounds": [r.__dict__ for r in outcome.rounds],
                "stopped_reason": outcome.stopped_reason,
            })
            # 若本层未产出任何结果（如模型未调用），停止后续层——
            # 后续层依赖的正是这些事实，没有事实就无从"套规则"。
            if not outcome.results:
                break

        patch: dict[str, Any] = {
            "model_tool_rounds": merged_rounds,
            "model_tool_names": merged_names,
            # 分层原因用 `L1:xxx | L2:yyy` 保留**每层各自的**停止原因。
            # 初版直接逗号拼接（如 'no_tool_call,budget_exhausted'），
            # 无法分辨哪层是哪种原因，排查时反而误导。
            "model_tool_stopped_reason": " | ".join(stop_reasons) or "no_tool_call",
            "model_tool_decisions": [d for t in layer_traces for d in t["decisions"]],
            # 按层保存的轨迹：供 runner 只把**本层**的决策附加到本层的 task 上，
            # 避免"每个 task 都显示全部层的决策"（初版缺陷）。
            "model_tool_layer_traces": layer_traces,
        }
        if merged_results:
            patch["task_results"] = merged_results
        return patch

    @staticmethod
    def _tag_layer_results(results: list[dict], *, layer_index: int,
                           layer_prefix: str, layer_intents: list[str],
                           prior_facts: str = "") -> list[dict]:
        """给某层的 task 结果打上**层标记**并保证 `task_id` 全局唯一。

        为什么需要：每层是**独立的** `loop.run()`，而循环内部按轮次生成 task_id
        （如 `model-tool-1`），导致不同层产生**同名 task_id**。实测 C4 用例输出中
        两个 task 都叫 `model-tool-1`，无法区分也无法对齐到具体层。
        故加层前缀（`L1-model-tool-1`），并在 data 里记录层号与本层意图，
        使"哪层查了什么"可直接从响应读出。

        `prior_facts`：本层执行时**已获得的**前序事实，写入 `data.prior_facts`，
        供 `generate_node` 在**生成阶段**使用（ISSUE-20260914-GRAPH-GENERATION-MISSING-PRIOR-FACTS）。
        为何放 data 而非 AgentState 顶层键：`task_results` 是**自由形态的 dict 列表**，
        其内部键不会被 LangGraph 过滤；而顶层未声明的键会被**静默丢弃**
        （见 ISSUE-20260914-AGENT-STATE-SILENTLY-DROPS-UNDECLARED-KEYS 的教训）。
        """
        tagged: list[dict] = []
        for item in results:
            row = dict(item)
            raw_id = str(row.get("task_id") or "")
            row["task_id"] = f"{layer_prefix}-{raw_id}" if raw_id else layer_prefix
            row["data"] = {
                **(row.get("data") or {}),
                "layer": layer_index,
                "layer_intents": list(layer_intents),
                "prior_facts": prior_facts,
            }
            tagged.append(row)
        return tagged

    @staticmethod
    def _facts_context(results: list[dict]) -> str:
        """把已完成 task 的答案拼成"前序事实"上下文。

        文案与 `CustomerTaskExecutor._dependency_context` **逐字一致**，
        避免同一语义出现两种说法（该模块已有同类教训）。
        """
        blocks: list[str] = []
        for item in results:
            answer = str(item.get("answer") or "").strip()
            if not answer:
                continue
            blocks.append(f"[{item.get('intent') or 'unknown'}] {answer}")
        if not blocks:
            return ""
        return ("\n\n【前序查询结果（请基于这些事实作答，不要臆造）】\n"
                + "\n".join(blocks))

    @staticmethod
    def _task_layers(tasks: list[Any]) -> list[list[Any]]:
        """按 `depends_on` 把 tasks 拓扑分层：同层可并行、层间串行。

        - 依赖已满足的 task 归入当前层；
        - 检测到**循环依赖**或依赖缺失时，把剩余 task 一并放入最后一层，
          保证不丢 task（宁可顺序退化为并行，也不静默丢弃）。
        """
        remaining = list(tasks)
        done: set[str] = set()
        layers: list[list[Any]] = []
        while remaining:
            ready = [t for t in remaining
                     if all(dep in done for dep in (getattr(t, "depends_on", None) or []))]
            if not ready:
                # 存在循环依赖/指向不存在的 task —— 全部置于最后一层，避免死循环
                layers.append(remaining)
                break
            layers.append(ready)
            done.update(t.task_id for t in ready)
            remaining = [t for t in remaining if t not in ready]
        return layers

    def _route_after_model_tool(self, state: AgentState) -> str:
        """模型工具轮拿到结果 → 先经 generate_node 生成答案；没拿到 → 回退确定性 ReAct。"""
        return "generate_node" if state.task_results else "react_step_node"

    # ---- 只读 ReAct 循环（确定性兜底：工具名硬编码 + 固定模板改写） ----
    def _react_task_index(self, state: AgentState) -> int:
        """当前循环正在处理第几个 task（已完成数 = len(react_results)）。"""
        return len(state.react_results)

    async def react_step_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """只读知识意图的 ReAct 一步：调用知识工具，结果哈希早停，未完成则改写查询回环。

        多意图：plan.tasks 可能含多个只读知识 task（如"公司"同时命中 company_info + company_business）。
        此前只取 tasks[0]，后续 task 会被静默丢弃（管线会全部执行）。现按 react_results 长度推进到
        下一个未完成的 task；每个 task 的结果累积进 react_results，最后由 react_done_node 汇总。
        """
        if self.react_tool is None:
            raise RuntimeError("read-only ReAct 需要注入 react_tool")
        plan = _rebuild_plan(state.plan)
        if not plan.tasks:
            return {"react_result": None, "react_results": list(state.react_results)}

        index = self._react_task_index(state)
        if index >= len(plan.tasks):
            # 所有 task 已处理完 → 结束循环
            return {"react_result": dict(state.react_results[-1]) if state.react_results else None}

        task = plan.tasks[index]
        query = state.react_query or task.query
        # 工具必须按 task 意图选择，不能一律走知识检索。
        # 此前硬编码 `search_customer_knowledge`，导致**事实类意图**（order_status/asset_summary/
        # workorder_status/risk_restriction/product_status）也被当作知识查询：
        # 实测"我的订单 ORD-123456 状态"回退到本节点后，用知识库查订单 → 20 轮全失败
        # → low_confidence → 误转人工（管线用 get_my_order_status 一次即得 fact_not_found
        # → needs_clarification，requires_handoff=False）。
        # 见 ISSUE-20260914-REACT-FALLBACK-IGNORES-INTENT。
        tool_name = TOOL_BY_INTENT.get(task.intent, "search_customer_knowledge")
        result = await self.react_tool.execute(
            name=tool_name, customer_id=state.customer_id or "",
            query=query, intent=task.intent, entities=task.arguments)
        attempts = state.react_attempts + [{
            "tool": tool_name, "iteration": len(state.react_attempts) + 1,
            "task_id": task.task_id, "intent": task.intent,
            "success": result.success, "error": result.error,
            "evidence_status": result.data.get("evidence_status") if result.data else None,
        }]
        sig = CustomerTaskExecutor._result_signature(result)
        # 早停哈希必须按 task 隔离：多意图时 t1/t2 常共用同一句 query（如"公司业务"同时命中
        # company_info + company_business），若共用 seen_signatures，第二个 task 会立刻被判
        # "结果重复、无进展"而早停成 needs_clarification → summarize 变 partially_completed。
        # 因此每个 task 用 {index: [sig,...]} 独立记录；state.seen_signatures 保留扁平列表供审计。
        per_task = dict(state.seen_signatures_by_task)
        task_seen = list(per_task.get(str(index), []))
        repeated = sig in task_seen
        if not repeated:
            task_seen.append(sig)
        per_task[str(index)] = task_seen
        seen = state.seen_signatures if repeated else state.seen_signatures + [sig]
        citations = (result.data.get("citations") or []) if result.data else []
        done = repeated or (result.success and citations) or len(attempts) >= self.max_iterations
        if done:
            ok = result.success and bool(citations)
            # 失败文案按**工具类型**区分（与管线 CustomerTaskExecutor._execute_one 对齐）：
            #   - 事实类工具（订单/资产/工单/风险限制/产品状态）：fact_not_found / ambiguous_product
            #     → "请补充更明确的查询对象或订单/产品信息"（要用户补充，**不转人工**）
            #   - 知识检索：沿用 RAG 的 handoff message（"当前知识库没有足够依据…"，会转人工）
            # 此前一律用知识检索文案，导致"订单查不到"被表述成"知识库没有依据+转人工"。
            is_fact_tool = result.error in {"fact_not_found", "ambiguous_product"}
            if ok:
                answer_text = result.answer
            elif is_fact_tool:
                answer_text = result.answer or result.error or ""
            else:
                answer_text = result.answer or _FALLBACK_NO_EVIDENCE
            clarification_text = None if ok else (
                "请补充更明确的查询对象或订单/产品信息。" if is_fact_tool else _FALLBACK_CLARIFICATION
            )
            finished = {
                "task_id": task.task_id, "intent": task.intent,
                "status": "succeeded" if ok else "needs_clarification",
                "answer": answer_text,
                "data": {**(result.data or {}), "execution_mode": "bounded_react", "tool_calls": attempts},
                "error": None if ok else (result.error or "tool_budget_exhausted"),
                "clarification": clarification_text,
            }
            accumulated = list(state.react_results) + [finished]
            is_last = index + 1 >= len(plan.tasks)
            return {
                "react_attempts": attempts, "seen_signatures": seen,
                "seen_signatures_by_task": per_task,
                "react_results": accumulated,
                # 只有全部 task 处理完才置 react_result，条件边据此决定是否继续回环
                "react_result": finished if is_last else None,
                # 进入下一个 task 时重置改写/查询，避免沿用上一个 task 的改写串
                "react_query": "" if not is_last else query,
            }
        idx = len(state.react_attempts)
        next_q = f"{task.query} {_REWRITE_POOL[idx]}" if idx < len(_REWRITE_POOL) else query
        return {"react_query": next_q, "react_attempts": attempts, "seen_signatures": seen}

    async def react_done_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        if state.react_results:
            return {"task_results": [dict(r) for r in state.react_results]}
        if state.react_result:
            return {"task_results": [dict(state.react_result)]}
        return {"task_results": []}

    async def generate_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """只读 ReAct 命中引用后，调 LLM 基于引用生成最终答案（补全只读链路）。

        多意图：对每个命中的 task 各生成一次答案，避免只有第一个 task 有 LLM 答复。
        """
        if not state.task_results or self.answer_generator is None:
            return {}
        out: list[dict] = []
        changed = False
        for raw in state.task_results:
            r = dict(raw)
            citations = (r.get("data") or {}).get("citations") or []
            if r.get("status") != "succeeded" or not citations:
                out.append(r)
                continue
            rag_result = CustomerRagResult(
                intent=r.get("intent", "faq"), answer_mode="rag_answer",
                evidence_status=(r.get("data") or {}).get("evidence_status", "sufficient"),
                citations=[KnowledgeCitation(
                    chunk_id=c.get("chunk_id") or c.get("id") or "",
                    document_id=c.get("document_id") or "",
                    version_id=c.get("version_id") or "",
                    content=c.get("content") or "",
                    score=c.get("score") or 0.0,
                    title_path=c.get("title_path") or "",
                    page_number=c.get("page_number"),
                    metadata=c.get("metadata") or {},
                ) for c in citations],
                handoff_required=False, message=r.get("answer", ""),
            )
            # 生成用的 query：**必须带上本层的前序事实**。
            #
            # ISSUE-20260914-GRAPH-GENERATION-MISSING-PRIOR-FACTS
            # 此前这里对所有 task 一律用 `state.react_query or state.user_message`，
            # 即风险制度 task 只拿到用户原问句"我有没有风险限制"，**看不到**
            # 前一层查到的"C4"。于是它只能讲通用规则，给不出"您可买 R1–R4"的结论，
            # 还会自认"资料未完全覆盖"（partially_answered）。
            # 而 pipeline 同一场景是对的——因为 `_bounded_react_knowledge` 会把
            # `item.dependency_context` 附到生成 query 上。此处补齐这一环。
            #
            # 事实来源：`model_tool_node` 分层时写入 task 的 `data.prior_facts`
            # （该键随 task 结果一起流转，因为 task_results 是**自由形态的 dict 列表**，
            #  不像 AgentState 的顶层键那样会被过滤）。
            # **只影响生成、不影响检索**：检索早已在 model_tool_node 完成。
            layer_facts = str((r.get("data") or {}).get("prior_facts") or "")
            gen_query = state.react_query or state.user_message
            if layer_facts:
                gen_query = f"{gen_query}{layer_facts}"
            generated = await self.answer_generator.generate(
                query=gen_query, result=rag_result)
            r["answer"] = generated.answer
            r["data"] = {**(r.get("data") or {}), "generation_status": generated.generation_status}
            # 中间态：拿到了引用、生成了答案，但答案**自述资料不足**（引用没覆盖问题本身）。
            # 此前只有 succeeded/failed 两态，这类"半成功"被标成 succeeded → summarize 判
            # `completed`，用户看到"资料不足"却被告知问题已解决
            # （ISSUE-20260914-NO-PARTIAL-STATE）。
            # 注意：不置 handoff（引用确实相关，只是不完整），仅标记状态供下游如实表达。
            if getattr(generated, "insufficient", False):
                r["status"] = "partially_answered"
                r["data"] = {**r["data"], "insufficient_evidence": True}
                if not r.get("clarification"):
                    r["clarification"] = "当前资料未完全覆盖该问题，可补充更具体的信息或转人工客服。"
            out.append(r)
            changed = True
        return {"task_results": out} if changed else {}

    async def persist_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """落库收尾：短时记忆回写、活跃产品长时记忆、情景记忆提炼入队。

        与管线 customer_agent_execution.py 的收尾段对齐。database 未注入时为空操作。
        """
        memory = self._memory()
        if memory is None or not state.conversation_id:
            return {"persisted": False}

        results = [TaskExecutionResult(**r) for r in state.task_results]
        active_product = _active_product_from_results(results) or state.active_product
        plan = _rebuild_plan(state.plan)

        # 只在需要时补建工单：summarize 判定 needs_handoff 但走的是 execute 分支（非 handoff_node）
        patch: dict[str, Any] = {}
        if state.needs_handoff and not state.ticket_no and state.customer_id:
            try:
                from app.services.customer_rag_handoff import CustomerRagHandoffService
                low_conf = any(r.error == "low_confidence" for r in results)
                handoff = await CustomerRagHandoffService(self.database).handoff(
                    customer_id=state.customer_id, query=state.combined_query or state.user_message,
                    intent=next((t.intent for t in plan.tasks if t.intent != "human_handoff"), "human_handoff"),
                    evidence_status="low_confidence" if low_conf else "customer_requested", citations=[])
                patch.update({
                    "work_order_id": handoff.get("work_order_id"),
                    "ticket_no": handoff.get("ticket_no"),
                    "customer_service_session_id": handoff.get("customer_service_session_id"),
                    "customer_service_session_status": handoff.get("customer_service_session_status"),
                })
            except Exception:  # noqa: BLE001
                pass

        handoff_state = None
        if state.work_order_id or patch.get("work_order_id"):
            handoff_state = {
                "work_order_id": state.work_order_id or patch.get("work_order_id"),
                "ticket_no": state.ticket_no or patch.get("ticket_no"),
                "customer_service_session_id": state.customer_service_session_id or patch.get("customer_service_session_id"),
                "customer_service_session_status": state.customer_service_session_status or patch.get("customer_service_session_status"),
            }

        try:
            if state.preference_short_circuit:
                # 纯偏好短路：与管线早退分支写同样的状态——清空本轮 task/澄清/工单，
                # 计数归零，避免把「只确认偏好」当成一次失败澄清而累积澄清次数。
                await memory.save_short_term(
                    customer_id=state.customer_id or "", conversation_id=state.conversation_id,
                    state={
                        "conversation_id": state.conversation_id,
                        "original_query": state.user_message,
                        "latest_query": state.user_message,
                        "task_results": [],
                        "plan_execution_modes": [],
                        "clarification": None,
                        "missing_parameter": False,
                        "clarification_count": 0,
                        "pending_intent_plan": {},
                        "handoff": None,
                        "active_product": active_product or "",
                    })
                return {"persisted": True, "clarification": None}
            await memory.save_short_term(
                customer_id=state.customer_id or "", conversation_id=state.conversation_id,
                state={
                    "conversation_id": state.conversation_id,
                    "original_query": state.combined_query or state.user_message,
                    "latest_query": state.user_message,
                    "task_results": list(state.task_results),
                    "plan_execution_modes": sorted({t.execution_mode for t in plan.tasks}),
                    "clarification": state.clarification,
                    "missing_parameter": bool(state.clarification),
                    "clarification_count": state.clarification_count,
                    "pending_intent_plan": {} if state.overall_status != "needs_clarification" else {
                        "tasks": [t.__dict__ for t in plan.tasks],
                        "execution_modes": sorted({t.execution_mode for t in plan.tasks}),
                    },
                    "handoff": handoff_state,
                    "active_product": active_product or "",
                })
        except Exception:  # noqa: BLE001 - 记忆写入失败不阻塞
            pass

        if active_product and state.is_customer:
            try:
                await memory.remember(
                    customer_id=state.customer_id or "", key="active_product", value=active_product,
                    memory_type="conversation_fact", source="conversation",
                    conversation_id=state.conversation_id)
            except Exception:  # noqa: BLE001
                pass

        # 情景记忆后台提炼：每轮入队 memory.summary_extract，由 memory_worker 递进落库
        try:
            from uuid import uuid4
            from app.models.outbox import OutboxEvent
            async with self.database.session_factory() as session:
                session.add(OutboxEvent(
                    id=str(uuid4()), event_type="memory.summary_extract",
                    aggregate_type="customer_memory", aggregate_id=state.customer_id,
                    payload_json={
                        "customer_id": state.customer_id, "conversation_id": state.conversation_id,
                        "original_query": state.combined_query or state.user_message,
                        "latest_query": state.user_message,
                        "task_results": [r.answer for r in results if r.answer][:5],
                        "active_product": active_product or "",
                        "handoff_reason": "low_confidence" if any(r.error == "low_confidence" for r in results) else "",
                    },
                    status="pending",
                ))
                await session.commit()
        except Exception:  # noqa: BLE001 - 摘要入队失败不阻塞
            pass

        return {"persisted": True, **patch}

    def _should_continue_react(self, state: AgentState) -> str:
        return "react_done_node" if state.react_result else "react_step_node"

    # ---- 条件路由 ----
    def _route_after_intent_plan(self, state: AgentState) -> str:
        """纯偏好自述短路时直接落库收尾，跳过 route/execute（与管线早退行为一致）。"""
        if state.preference_short_circuit:
            return "persist_node"
        return "route_node"

    # 只有这些"只读查询"意图才允许进入模型自主工具轮。
    # 其余意图都有**专用确定性路径**，交给知识检索工具处理会产生错误结果
    # （ISSUE-20260914-MODEL-TOOL-MISROUTES-NON-RAG-INTENTS）：
    #   - product_recommendation → SuitabilityService（实时 Product + 适当性），走 RAG 只会拿到
    #     "产品推荐使用实时 Product 数据…"这类短路占位串被当成答案；
    #   - greeting → CustomerTaskExecutor 的固定话术，走 RAG 变成"检索+生成"多绕一圈，
    #     且生成失败时用户看到"当前回答服务暂时不可用"；
    #   - human_handoff → 直接转人工；business_operation → 交易草稿（绝不进只读工具）。
    MODEL_TOOL_INTENTS = frozenset({
        "asset_summary", "order_status", "workorder_status", "risk_restriction", "product_status",
        "company_info", "company_business", "faq", "workflow", "policy", "risk_public",
    })

    @classmethod
    def _model_tool_applicable(cls, plan: IntentPlan) -> bool:
        """plan 是否全部由"模型工具轮可处理的只读查询意图"构成。"""
        tasks = getattr(plan, "tasks", None) or []
        return bool(tasks) and all(t.intent in cls.MODEL_TOOL_INTENTS for t in tasks)

    def _route_after_plan(self, state: AgentState) -> str:
        """按意图类型与澄清状态决定下一节点：模型工具轮 / 确定性 ReAct / 确定性执行 / clarify / handoff。

        只读查询优先走模型自主工具调度（若已启用并注入了 factory）；否则回退确定性 ReAct。
        **意图级门禁**：非只读查询意图（推荐/问候/转人工/交易草稿）一律不进模型工具轮，
        它们各有专用确定性路径，交给知识检索工具会产出错误答案。
        """
        if state.plan.get("clarification"):
            if state.clarification_count + 1 >= 2:
                return "handoff_node"
            return "clarify_node"
        plan = _rebuild_plan(state.plan)
        if self._model_tool_selection_enabled() and self._model_tool_applicable(plan):
            return "model_tool_node"
        if self.react_tool is not None and self._is_readonly_knowledge(plan):
            return "react_step_node"
        return "execute_node"

    def build(self, checkpointer: Any | None = None, *, interrupt_before: list[str] | None = None,
              interrupt_after: list[str] | None = None) -> CompiledStateGraph:
        """编译图；checkpointer 传 LangGraph Checkpointer（MemorySaver / AsyncPostgresSaver）即支持断点续跑；
        interrupt_before/after 指定中断点（配合 checkpointer 做断点续跑演示/门控）。"""
        g = StateGraph(AgentState)
        g.add_node("intent_plan_node", self.intent_plan_node)
        g.add_node("route_node", self.route_node)
        g.add_node("execute_node", self.execute_node)
        g.add_node("summarize_node", self.summarize_node)
        g.add_node("clarify_node", self.clarify_node)
        g.add_node("handoff_node", self.handoff_node)
        g.add_node("react_step_node", self.react_step_node)
        g.add_node("react_done_node", self.react_done_node)
        g.add_node("model_tool_node", self.model_tool_node)
        g.add_node("generate_node", self.generate_node)
        g.add_node("persist_node", self.persist_node)

        g.add_edge(START, "intent_plan_node")
        # 纯偏好自述短路 → 直接 persist；其余走正常编排
        g.add_conditional_edges("intent_plan_node", self._route_after_intent_plan, {
            "persist_node": "persist_node", "route_node": "route_node",
        })
        g.add_conditional_edges("route_node", self._route_after_plan, {
            "clarify_node": "clarify_node", "handoff_node": "handoff_node",
            "execute_node": "execute_node", "react_step_node": "react_step_node",
            "model_tool_node": "model_tool_node",
        })
        # 模型工具轮：拿到结果先经 generate_node 生成答案，再 summary；没拿到则回退确定性 ReAct。
        # 必须走 generate_node —— 否则 search_customer_knowledge 返回的只是 RAG 服务的
        # message 占位串（"可基于客服知识库引用生成回答。"），用户拿到的是占位而非真实答案
        # （ISSUE-20260914-MODEL-TOOL-SKIPS-ANSWER-GENERATION）。
        g.add_conditional_edges("model_tool_node", self._route_after_model_tool, {
            "generate_node": "generate_node", "react_step_node": "react_step_node",
        })
        g.add_conditional_edges("react_step_node", self._should_continue_react, {
            "react_step_node": "react_step_node", "react_done_node": "react_done_node",
        })
        g.add_edge("react_done_node", "generate_node")
        g.add_edge("generate_node", "summarize_node")
        g.add_edge("execute_node", "summarize_node")
        # summarize 后统一落库收尾；handoff 已自行建单，clarify 需回写澄清态，两者都进 persist
        g.add_edge("summarize_node", "persist_node")
        g.add_edge("persist_node", END)
        g.add_edge("clarify_node", "persist_node")
        g.add_edge("handoff_node", "persist_node")
        return g.compile(checkpointer=checkpointer, interrupt_before=interrupt_before, interrupt_after=interrupt_after)
