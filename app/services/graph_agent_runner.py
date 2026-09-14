"""SupervisorGraph 执行入口：把图跑出来的 AgentState 映射回 API 的响应契约。

设计约束（重要）：
- **不改变 API 响应契约**。图跑完得到的 state 组装成原有 `CustomerTaskExecutionResponse`，
  前端 / WebSocket / E2E 脚本无需改动。
- **影子模式绝不抛异常穿透**。影子双跑发生在真实请求的旁路，任何图侧异常都只写审计，
  不能影响已经决定返回给用户的管线结果。
- 图与管线共享同一套 handler（planner / executor / handoff / memory），所以对照的是
  "编排层"差异，而不是业务逻辑差异。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from app.common.logging.config import get_logger
from app.schemas.agents import AgentState
from app.schemas.customer_service import CustomerTaskExecutionResponse, TaskExecutionResultResponse

logger = get_logger(__name__)


def _results_from_state(state: dict) -> list[TaskExecutionResultResponse]:
    """把图状态里的 task_results 转成响应，并**附加模型工具调度轨迹**。

    为什么把轨迹塞进每个 task 的 data：`model_tool_rounds` / `model_tool_decisions` 只存在
    图状态里，而 API 响应与排查脚本都只看 `results[].data`，导致"模型为什么多调一个工具"
    在图外完全不可见——只能靠猜。这里把它随结果一并带出，保证**留痕可查**。
    仅在模型自主调度真的跑过时才附加，避免污染确定性路径的响应体。
    """
    rounds = state.get("model_tool_rounds") or []
    decisions = state.get("model_tool_decisions") or []
    stopped_reason = state.get("model_tool_stopped_reason") or ""
    trace = (
        {"rounds": rounds, "decisions": decisions, "stopped_reason": stopped_reason}
        if (rounds or decisions or stopped_reason) else {}
    )
    out: list[TaskExecutionResultResponse] = []
    for item in (state.get("task_results") or []):
        data = dict(item.get("data") or {})
        if trace and "model_tool_trace" not in data:
            data["model_tool_trace"] = trace
        out.append(TaskExecutionResultResponse(
            task_id=item.get("task_id", ""),
            intent=item.get("intent", ""),
            status=item.get("status", ""),
            answer=item.get("answer", "") or "",
            data=data,
            error=item.get("error"),
            clarification=item.get("clarification"),
        ))
    return out


def response_from_state(*, state: dict, conversation_id: str) -> CustomerTaskExecutionResponse:
    """AgentState（图输出，dict 形式）→ CustomerTaskExecutionResponse。

    answer 的口径要与管线一致：管线把 summarize 的聚合答案与"已转人工，工单号：X"
    拼成多行；图把这些放在 final_response 与 ticket_no 里，这里复原同样的拼接。
    """
    lines: list[str] = []
    final = state.get("final_response") or ""
    if final:
        lines.append(final)
    ticket_no = state.get("ticket_no")
    if state.get("needs_handoff") and ticket_no and str(ticket_no) not in final:
        lines.append(f"已为您转接人工客服，工单号：{ticket_no}。")

    return CustomerTaskExecutionResponse(
        conversation_id=conversation_id,
        overall_status=state.get("overall_status") or "needs_clarification",
        answer="\n".join(lines) or "当前没有可返回的结果，建议转人工客服。",
        route=state.get("route") or "customer_service",
        agents=list(state.get("selected_agents") or ["CustomerServiceAgent"]),
        results=_results_from_state(state),
        clarification=state.get("clarification"),
        requires_handoff=bool(state.get("needs_handoff")),
        work_order_id=state.get("work_order_id"),
        ticket_no=ticket_no,
        customer_service_session_id=state.get("customer_service_session_id"),
        customer_service_session_status=state.get("customer_service_session_status"),
    )


def state_from_request(*, customer_id: str, conversation_id: str, query: str, user_id: str | None = None,
                       role: str | None = None, is_customer: bool = True, request_id: str | None = None,
                       trace_id: str = "") -> AgentState:
    return AgentState(
        request_id=request_id or str(uuid4()),
        trace_id=trace_id,
        user_id=user_id,
        customer_id=customer_id,
        role=role,
        user_message=query,
        conversation_id=conversation_id,
        is_customer=is_customer,
    )


# 影子对照关注的字段：编排决策与最终产物，不含时间戳/内部计数
SHADOW_COMPARE_FIELDS = ("overall_status", "route", "answer", "clarification", "requires_handoff")


def shadow_diff(pipeline: CustomerTaskExecutionResponse, graph: CustomerTaskExecutionResponse) -> dict:
    """比对图版与管线版响应，返回差异字典；无差异返回 {}。"""
    diffs: dict[str, Any] = {}
    for field in SHADOW_COMPARE_FIELDS:
        left, right = getattr(pipeline, field, None), getattr(graph, field, None)
        if left != right:
            diffs[field] = {"pipeline": left, "graph": right}
    p_intents = [item.intent for item in pipeline.results]
    g_intents = [item.intent for item in graph.results]
    if p_intents != g_intents:
        diffs["task_intents"] = {"pipeline": p_intents, "graph": g_intents}
    return diffs


async def _audit(database, *, customer_id: str, conversation_id: str, action: str, detail: dict) -> None:
    from app.models.audit import AuditLog
    async with database.session_factory() as session:
        session.add(AuditLog(
            id=str(uuid4()), actor_user_id=customer_id, action=action,
            resource_type="customer_service_conversation", resource_id=conversation_id,
            detail=json.dumps(detail, ensure_ascii=False),
        ))
        await session.commit()


class GraphAgentRunner:
    """把 SupervisorGraph 包成与 CustomerAgentExecutionService 同形的执行入口。

    - `execute()`：图作为权威路径（agent_graph_enabled=True 时 API 用它）。
    - `run_shadow()`：图作为旁路对照（agent_graph_shadow_enabled=True），
      结果只写审计，绝不返回给用户、绝不抛异常穿透。
    """

    def __init__(self, database, redis_client, *, settings, checkpointer: Any | None = None,
                 executor: Any | None = None) -> None:
        self.database = database
        self.redis_client = redis_client
        self.settings = settings
        self.checkpointer = checkpointer
        self.executor = executor
        self._graph = None

    def _compiled(self):
        if self._graph is None:
            from app.agents.graph import SupervisorGraph
            from app.services.customer_model_tools import CustomerModelToolLoop
            from app.services.customer_rag_answer import CustomerRagAnswerService
            from app.services.customer_tools import CustomerReadTools
            from app.infrastructure.qwen import QwenProvider

            provider = QwenProvider(self.settings)
            read_tools = CustomerReadTools(self.database)

            def _model_tool_factory(*, max_rounds: int):
                """模型自主工具调度：只读工具白名单 + 有界轮数 + 全程审计。

                只读查询交由模型决定调哪个工具；资金/风控仍是确定性路径
                （写操作不在工具集中，模型即使编造工具名也会被硬白名单拒绝）。
                """
                return CustomerModelToolLoop(
                    provider=provider, tools=read_tools, database=self.database,
                    max_rounds=max_rounds, model=None)

            graph = SupervisorGraph(
                executor=self.executor,
                react_tool=read_tools,
                answer_generator=CustomerRagAnswerService(provider),
                database=self.database,
                redis_client=self.redis_client,
                model_tool_loop_factory=_model_tool_factory,
                max_iterations=self.settings.agent_graph_max_iterations,
                max_tool_calls=self.settings.agent_graph_max_tool_calls,
            )
            self._graph = graph.build(checkpointer=self.checkpointer)
        return self._graph

    async def execute(self, *, customer_id: str, conversation_id: str, query: str,
                      is_customer: bool = True, user_id: str | None = None,
                      role: str | None = None) -> CustomerTaskExecutionResponse:
        state = state_from_request(
            customer_id=customer_id, conversation_id=conversation_id, query=query,
            user_id=user_id, role=role, is_customer=is_customer)
        config = None
        if self.checkpointer is not None:
            from app.agents.checkpointer import thread_config
            config = thread_config(conversation_id)
        final = await self._compiled().ainvoke(state, config)
        return response_from_state(state=dict(final), conversation_id=conversation_id)

    async def run_shadow(self, *, customer_id: str, conversation_id: str, query: str,
                         pipeline_result: CustomerTaskExecutionResponse,
                         is_customer: bool = True, timeout_seconds: float = 20.0) -> dict | None:
        """旁路跑一遍图并与管线结果对照，差异写 AuditLog。任何异常都被吞掉。"""
        try:
            graph_result = await asyncio.wait_for(
                self.execute(customer_id=customer_id, conversation_id=conversation_id, query=query,
                             is_customer=is_customer),
                timeout=timeout_seconds)
            diff = shadow_diff(pipeline_result, graph_result)
            await _audit(
                self.database, customer_id=customer_id, conversation_id=conversation_id,
                action="agent.graph_shadow_diff" if diff else "agent.graph_shadow_match",
                detail={
                    "query_length": len(query or ""),
                    "diff": diff,
                    "graph_status": graph_result.overall_status,
                    "pipeline_status": pipeline_result.overall_status,
                    "graph_route": graph_result.route,
                    "pipeline_route": pipeline_result.route,
                    "checkpoint_enabled": self.checkpointer is not None,
                })
            return diff
        except Exception as exc:  # noqa: BLE001 - 影子绝不穿透
            logger.warning("agent_graph_shadow_failed conversation_id=%s err=%s", conversation_id,
                           f"{type(exc).__name__}: {exc}")
            try:
                await _audit(
                    self.database, customer_id=customer_id, conversation_id=conversation_id,
                    action="agent.graph_shadow_error",
                    detail={"error": f"{type(exc).__name__}: {exc}"[:2000]})
            except Exception:  # noqa: BLE001
                pass
            return None
