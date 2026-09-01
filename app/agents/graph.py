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
    def plan(self, query: str) -> IntentPlan: ...


class TaskExecutor(Protocol):
    async def execute(self, *, customer_id: str, plan: IntentPlan) -> list[TaskExecutionResult]: ...


class ReadOnlyTool(Protocol):
    """只读知识工具（read-only ReAct 循环每轮调用），与 CustomerReadTools.execute 同签名。"""

    async def execute(self, *, name: str, customer_id: str, query: str, intent: str, entities: dict) -> Any: ...


class AnswerGenerator(Protocol):
    """只读命中引用后的 LLM 答案生成，与 CustomerRagAnswerService.generate 同签名。"""

    async def generate(self, *, query: str, result: CustomerRagResult) -> Any: ...


_REWRITE_POOL = ["请提供官方流程、适用条件和注意事项", "请说明相关制度或流程，并给出依据"]


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


class SupervisorGraph:
    """真实编排 LangGraph 图：把确定性编排管线映射成可编译/可运行的状态图。

    节点：intent_plan → route → (澄清条件分支：clarify / handoff / execute) → summarize。
    每个节点复用现有确定性 handler（CustomerIntentPlanner / route_for_plan /
    CustomerTaskExecutor / CustomerTaskExecutor.summarize），故"图"与"确定性管线"
    共享同一套业务逻辑，只是用 LangGraph 显式表达节点、边与条件路由。
    planner / executor 可注入（测试传 fake），默认用真实实现。
    """

    def __init__(self, *, planner: PlanProvider | None = None, executor: TaskExecutor | None = None,
                 react_tool: ReadOnlyTool | None = None, answer_generator: AnswerGenerator | None = None,
                 max_iterations: int = 4, max_tool_calls: int = 8) -> None:
        self.planner = planner or CustomerIntentPlanner()
        self.executor = executor
        self.react_tool = react_tool
        self.answer_generator = answer_generator
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls

    @staticmethod
    def _is_readonly_knowledge(plan: IntentPlan) -> bool:
        """只读知识意图（走 LangGraph ReAct 循环）；其余（交易/风控等）走确定性节点。"""
        return bool(plan.tasks) and all(t.intent in CustomerTaskExecutor.REACT_INTENTS for t in plan.tasks)

    # ---- 节点 ----
    async def intent_plan_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        plan = self.planner.plan(state.user_message)
        return {"plan": _plan_to_dict(plan)}

    async def route_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        plan = _rebuild_plan(state.plan)
        route, agents = route_for_plan(plan)
        return {"route": route, "selected_agents": list(agents)}

    async def execute_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        if self.executor is None:
            raise RuntimeError("SupervisorGraph 缺少 executor（真实运行需注入 CustomerTaskExecutor）")
        plan = _rebuild_plan(state.plan)
        results = await self.executor.execute(customer_id=state.customer_id or "", plan=plan)
        return {"task_results": [r.__dict__ for r in results]}

    async def summarize_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        results = [TaskExecutionResult(**r) for r in state.task_results]
        overall, clarification, aggregate_answer = CustomerTaskExecutor.summarize(
            results, requires_handoff=_rebuild_plan(state.plan).requires_handoff)
        needs_handoff = _rebuild_plan(state.plan).requires_handoff or any(r.error == "low_confidence" for r in results)
        return {
            "overall_status": overall,
            "clarification": clarification,
            "final_response": aggregate_answer or "当前没有可返回的结果，建议转人工客服。",
            "needs_handoff": needs_handoff,
        }

    async def clarify_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        return {
            "overall_status": "needs_clarification",
            "clarification_count": state.clarification_count + 1,
            "final_response": (state.plan or {}).get("clarification") or "请补充信息。",
        }

    async def handoff_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        return {
            "overall_status": "handoff",
            "clarification_count": state.clarification_count + 1,
            "final_response": "为避免反复追问，已为您转接人工客服。",
            "needs_handoff": True,
        }

    # ---- 只读 ReAct 循环（LangGraph 条件回环） ----
    async def react_step_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """只读知识意图的 ReAct 一步：调用知识工具，结果哈希早停，未完成则改写查询回环。"""
        if self.react_tool is None:
            raise RuntimeError("read-only ReAct 需要注入 react_tool")
        plan = _rebuild_plan(state.plan)
        task = plan.tasks[0]
        query = state.react_query or task.query
        result = await self.react_tool.execute(
            name="search_customer_knowledge", customer_id=state.customer_id or "",
            query=query, intent=task.intent, entities=task.arguments)
        sig = CustomerTaskExecutor._result_signature(result)
        attempts = state.react_attempts + [{
            "tool": "search_customer_knowledge", "iteration": len(state.react_attempts) + 1,
            "success": result.success, "error": result.error,
            "evidence_status": result.data.get("evidence_status") if result.data else None,
        }]
        repeated = sig in state.seen_signatures
        seen = state.seen_signatures if repeated else state.seen_signatures + [sig]
        citations = (result.data.get("citations") or []) if result.data else []
        done = repeated or (result.success and citations) or len(attempts) >= self.max_iterations
        if done:
            ok = result.success and bool(citations)
            return {
                "react_attempts": attempts, "seen_signatures": seen,
                "react_result": {
                    "task_id": task.task_id, "intent": task.intent,
                    "status": "succeeded" if ok else "needs_clarification",
                    "answer": result.answer if ok else "当前资料不足。",
                    "data": {**(result.data or {}), "execution_mode": "bounded_react", "tool_calls": attempts},
                    "error": None if ok else (result.error or "tool_budget_exhausted"),
                    "clarification": None if ok else "当前资料不足，可以补充问题或转人工客服。",
                },
            }
        idx = len(state.react_attempts)
        next_q = f"{task.query} {_REWRITE_POOL[idx]}" if idx < len(_REWRITE_POOL) else query
        return {"react_query": next_q, "react_attempts": attempts, "seen_signatures": seen}

    async def react_done_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        if state.react_result:
            return {"task_results": [dict(state.react_result)]}
        return {"task_results": []}

    async def generate_node(self, state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
        """只读 ReAct 命中引用后，调 LLM 基于引用生成最终答案（补全只读链路）。"""
        if not state.task_results or self.answer_generator is None:
            return {}
        r = dict(state.task_results[0])
        citations = (r.get("data") or {}).get("citations") or []
        if r.get("status") == "succeeded" and citations:
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
            generated = await self.answer_generator.generate(query=state.react_query or state.user_message, result=rag_result)
            r["answer"] = generated.answer
            r["data"] = {**(r.get("data") or {}), "generation_status": generated.generation_status}
            return {"task_results": [r]}
        return {}

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
        g.add_edge("summarize_node", END)
        g.add_edge("clarify_node", END)
        g.add_edge("handoff_node", END)
        return g.compile(checkpointer=checkpointer, interrupt_before=interrupt_before, interrupt_after=interrupt_after)
