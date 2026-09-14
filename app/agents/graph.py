from typing import Any, Protocol

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.schemas.agents import AgentState
from app.services.customer_intent import CustomerIntentPlanner, IntentPlan, IntentTask, execution_mode_for_intent
from app.services.customer_agent_execution import route_for_plan
from app.services.customer_rag_service import CustomerRagResult
from app.services.customer_task_executor import CustomerTaskExecutor, TaskExecutionResult
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
                 max_iterations: int = 4, max_tool_calls: int = 8) -> None:
        self.planner = planner or CustomerIntentPlanner()
        self.executor = executor
        self.react_tool = react_tool
        self.answer_generator = answer_generator
        self.database = database
        self.redis_client = redis_client
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls

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

    # ---- 只读 ReAct 循环（LangGraph 条件回环） ----
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
        result = await self.react_tool.execute(
            name="search_customer_knowledge", customer_id=state.customer_id or "",
            query=query, intent=task.intent, entities=task.arguments)
        sig = CustomerTaskExecutor._result_signature(result)
        attempts = state.react_attempts + [{
            "tool": "search_customer_knowledge", "iteration": len(state.react_attempts) + 1,
            "task_id": task.task_id, "intent": task.intent,
            "success": result.success, "error": result.error,
            "evidence_status": result.data.get("evidence_status") if result.data else None,
        }]
        repeated = sig in state.seen_signatures
        seen = state.seen_signatures if repeated else state.seen_signatures + [sig]
        citations = (result.data.get("citations") or []) if result.data else []
        done = repeated or (result.success and citations) or len(attempts) >= self.max_iterations
        if done:
            ok = result.success and bool(citations)
            finished = {
                "task_id": task.task_id, "intent": task.intent,
                "status": "succeeded" if ok else "needs_clarification",
                "answer": result.answer if ok else "当前资料不足。",
                "data": {**(result.data or {}), "execution_mode": "bounded_react", "tool_calls": attempts},
                "error": None if ok else (result.error or "tool_budget_exhausted"),
                "clarification": None if ok else "当前资料不足，可以补充问题或转人工客服。",
            }
            accumulated = list(state.react_results) + [finished]
            is_last = index + 1 >= len(plan.tasks)
            return {
                "react_attempts": attempts, "seen_signatures": seen,
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
            generated = await self.answer_generator.generate(
                query=state.react_query or state.user_message, result=rag_result)
            r["answer"] = generated.answer
            r["data"] = {**(r.get("data") or {}), "generation_status": generated.generation_status}
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
    def _route_after_plan(self, state: AgentState) -> str:
        """按意图类型与澄清状态决定下一节点：read-only ReAct / 确定性执行 / clarify / handoff。"""
        if state.plan.get("clarification"):
            if state.clarification_count + 1 >= 2:
                return "handoff_node"
            return "clarify_node"
        plan = _rebuild_plan(state.plan)
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
        g.add_node("generate_node", self.generate_node)
        g.add_node("persist_node", self.persist_node)

        g.add_edge(START, "intent_plan_node")
        g.add_edge("intent_plan_node", "route_node")
        g.add_conditional_edges("route_node", self._route_after_plan, {
            "clarify_node": "clarify_node", "handoff_node": "handoff_node",
            "execute_node": "execute_node", "react_step_node": "react_step_node",
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
