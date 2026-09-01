from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import current_user, websocket_current_user
from app.models.audit import AuditLog
from app.models.auth import Role, User
from app.models.customer_service import CustomerServiceMessage, CustomerServiceSession
from app.models.workorder import WorkOrder
from app.schemas.customer_service import CustomerIntentPlanResponse, IntentTaskResponse, TaskExecutionResultResponse, CustomerTaskExecutionResponse, CustomerServiceActionRequest, UnifiedAgentRequest, CustomerServiceAssignRequest, CustomerServiceMessageCreateRequest, CustomerServiceMessageResponse, CustomerServiceSessionResponse
from app.services.customer_intent import CustomerIntentPlanner
from app.services.memory_service import CustomerMemoryService
from app.services.customer_agent_execution import CustomerAgentExecutionService
from app.services.agent_stream import AgentStreamEvent, display_chunks
from app.services.customer_tools import CustomerReadTools
from app.services.customer_task_executor import CustomerTaskExecutor
from app.services.customer_rag_handoff import CustomerRagHandoffService
from app.services.notification_service import enqueue_customer_service_notification
from app.services.customer_realtime import publish_customer_realtime
from app.services.agent_capabilities import AgentCapabilityRegistry
from app.services.data_analysis_agent import DataAnalysisAgent
from app.services.data_catalog_service import CatalogSearchScope, DataCatalogService
from app.services.permission_scoped_cache import PermissionScopedCache
from app.services.subject_scope import SubjectScopeService
from app.repositories.data_catalog import DataCatalogRepository
from app.models.data_catalog import DataCatalogColumn, DataCatalogExampleQuery, DataCatalogTable

router = APIRouter(prefix="/customer-service/sessions", tags=["customer-service"] )
ACTIVE_STATUSES = {"human_active"}
RECLAIMABLE_STATUSES = {"waiting_human", "paused"}
TERMINAL_STATUSES = {"resolved", "closed", "expired"}


def _is_customer(user: User) -> bool:
    return any(role.code == "customer" for role in user.roles)


def _permissions(user: User) -> set[str]:
    return {permission.code for role in user.roles for permission in role.permissions}


def _is_supervisor(user: User) -> bool:
    return user.is_super_admin or "customer_service:assign" in _permissions(user)


def _is_agent(user: User) -> bool:
    return not _is_customer(user) and (user.is_super_admin or "customer_service:accept" in _permissions(user))


def _require_agent(user: User) -> None:
    if not _is_agent(user):
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_AGENT_REQUIRED", "message": "customer service agent permission required"})


def _response(row: CustomerServiceSession) -> CustomerServiceSessionResponse:
    return CustomerServiceSessionResponse.model_validate(row, from_attributes=True)


def _audit(session, *, user_id: str, action: str, row: CustomerServiceSession, detail: str = "") -> None:
    session.add(AuditLog(id=str(uuid4()), actor_user_id=user_id, action=action, resource_type="customer_service_session", resource_id=row.id, detail=detail))


async def _get_agent(session, agent_id: str) -> User | None:
    return (await session.execute(select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == agent_id, User.status == "active"))).scalar_one_or_none()


async def _ensure_agent_free(session, agent_id: str, *, exclude_session_id: str | None = None) -> None:
    query = select(CustomerServiceSession).where(CustomerServiceSession.assigned_agent_id == agent_id, CustomerServiceSession.status.in_(ACTIVE_STATUSES))
    if exclude_session_id:
        query = query.where(CustomerServiceSession.id != exclude_session_id)
    existing = (await session.execute(query.with_for_update())).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_AGENT_BUSY", "message": "客服当前已有一个人工会话，不能再次接入"})


def _message_response(row: CustomerServiceMessage) -> CustomerServiceMessageResponse:
    return CustomerServiceMessageResponse.model_validate(row, from_attributes=True)


async def _session_for_message(db_session, session_id: str, user: User, *, lock: bool = False) -> CustomerServiceSession:
    query = select(CustomerServiceSession).where(CustomerServiceSession.id == session_id)
    if lock:
        query = query.with_for_update()
    row = (await db_session.execute(query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_SERVICE_SESSION_NOT_FOUND", "message": "session not found"})
    if _is_customer(user):
        if row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_SESSION_SCOPE", "message": "session is outside customer scope"})
    elif not _is_agent(user) and not user.is_super_admin:
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_AGENT_REQUIRED", "message": "customer service agent permission required"})
    elif not user.is_super_admin and row.assigned_agent_id != user.id:
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_SESSION_ASSIGNEE_REQUIRED", "message": "only assigned agent can access this session"})
    return row


@router.get("/{session_id}", response_model=ApiResponse[CustomerServiceSessionResponse])
async def get_session(request: Request, session_id: str, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as session:
        row = await _session_for_message(session, session_id, user)
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.get("/{session_id}/messages", response_model=ApiResponse[list[CustomerServiceMessageResponse]])
async def list_messages(request: Request, session_id: str, user: Annotated[User, Depends(current_user)], after: str | None = Query(None)):
    async with request.app.state.database.session_factory() as session:
        await _session_for_message(session, session_id, user)
        query = select(CustomerServiceMessage).where(CustomerServiceMessage.session_id == session_id)
        if after:
            cursor = await session.get(CustomerServiceMessage, after)
            if cursor is not None:
                if cursor.session_id != session_id:
                    raise HTTPException(status_code=400, detail={"code": "CUSTOMER_SERVICE_MESSAGE_CURSOR_INVALID", "message": "after cursor does not belong to this session"})
                query = query.where(
                    or_(
                        CustomerServiceMessage.created_at > cursor.created_at,
                        (CustomerServiceMessage.created_at == cursor.created_at) & (CustomerServiceMessage.id > cursor.id),
                    )
                )
        rows = list((await session.execute(query.order_by(CustomerServiceMessage.created_at.asc(), CustomerServiceMessage.id.asc()).limit(200))).scalars().all())
        return ApiResponse(data=[_message_response(row) for row in rows], trace_id=get_trace_id())


@router.post("/{session_id}/messages", response_model=ApiResponse[CustomerServiceMessageResponse], status_code=201)
async def create_message(request: Request, session_id: str, payload: CustomerServiceMessageCreateRequest, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as session:
        row = await _session_for_message(session, session_id, user, lock=True)
        if row.status in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "closed session cannot receive messages"})
        if _is_customer(user):
            sender_type, source = "customer", "customer_input"
        else:
            if row.status != "human_active" or row.assigned_agent_id != user.id:
                raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_HUMAN_NOT_ACTIVE", "message": "agent must claim the session before replying"})
            sender_type, source = "human_agent", "human_reply"
        message = CustomerServiceMessage(id=str(uuid4()), session_id=row.id, sender_type=sender_type, sender_user_id=user.id, content=payload.content.strip(), message_type="text", source=source)
        session.add(message)
        if sender_type == "customer":
            await enqueue_customer_service_notification(session, user_id=row.assigned_agent_id, notification_type="customer_service_message", title="客户发送了新消息", content="实时客服会话中有新的客户消息，请及时查看。", session_id=row.id, event_key=f"customer-service-message:{message.id}:{row.assigned_agent_id}")
        else:
            await enqueue_customer_service_notification(session, user_id=row.customer_id, notification_type="customer_service_message", title="人工客服回复了您", content="您的人工客服会话收到新回复，请打开客服窗口查看。", session_id=row.id, event_key=f"customer-service-message:{message.id}:{row.customer_id}")
        row.last_message_at = datetime.now(timezone.utc)
        _audit(session, user_id=user.id, action="customer_service_message_create", row=row, detail=json.dumps({"sender_type": sender_type}, ensure_ascii=False))
        await session.commit(); await session.refresh(message)
        if sender_type == 'human_agent':
            await publish_customer_realtime(request.app.state.redis.client, row.customer_id, 'human.message.received', row.id, {'message': _message_response(message).model_dump(mode='json')})
        return ApiResponse(data=_message_response(message), trace_id=get_trace_id())


@router.post("/intent-plan", response_model=ApiResponse[CustomerIntentPlanResponse])
async def intent_plan(request: Request, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    conversation_id = request.headers.get("X-Conversation-Id") or str(uuid4())
    memory = CustomerMemoryService(request.app.state.database, request.app.state.redis.client)
    short_term = await memory.load_short_term(customer_id=user.id, conversation_id=conversation_id)
    combined_query = payload.note.strip()
    previous_clarification = short_term.get("clarification")
    if previous_clarification and short_term.get("missing_parameter") and combined_query:
        combined_query = f"{short_term.get('original_query', '')} {combined_query}".strip()
    plan = CustomerIntentPlanner().plan(combined_query)
    state = {
        **short_term, "conversation_id": conversation_id, "original_query": short_term.get("original_query") or payload.note.strip(),
        "latest_query": payload.note.strip(), "intent_plan": {"tasks": [item.__dict__ for item in plan.tasks], "clarification": plan.clarification},
        "clarification": plan.clarification, "missing_parameter": bool(plan.clarification),
    }
    await memory.save_short_term(customer_id=user.id, conversation_id=conversation_id, state=state)
    return ApiResponse(data=CustomerIntentPlanResponse(
        normalized_query=plan.normalized_query,
        tasks=[IntentTaskResponse(
            task_id=item.task_id, intent=item.intent, route=item.route, query=item.query,
            priority=item.priority, depends_on=item.depends_on, required=item.required,
            arguments=item.arguments, confidence=item.confidence, status=item.status, execution_mode=item.execution_mode,
        ) for item in plan.tasks],
        clarification=plan.clarification, requires_handoff=plan.requires_handoff,
    ), trace_id=get_trace_id())


@router.post("/agent/execute", response_model=ApiResponse[CustomerTaskExecutionResponse])
async def execute_unified_agent(request: Request, payload: UnifiedAgentRequest, user: Annotated[User, Depends(current_user)]):
    conversation_id = payload.conversation_id or request.headers.get("X-Conversation-Id") or str(uuid4())
    registry = AgentCapabilityRegistry()
    available_agents = registry.available_for(user)
    denied_agents = registry.denied_for(user)
    analysis = None
    analysis_refusal_reason = None
    template_attempt_reason = "agent_unavailable"
    template_attempt_id = None
    if "DataAnalysisAgent" in available_agents:
        analyzer = DataAnalysisAgent()
        query_is_safe, query_safety_reason = analyzer.evaluate_query_safety(payload.content)
        if not query_is_safe:
            analysis_refusal_reason = query_safety_reason
            template_attempt_reason = query_safety_reason
            async with request.app.state.database.session_factory() as audit_session:
                audit_session.add(AuditLog(
                    id=str(uuid4()),
                    actor_user_id=user.id,
                    action="data_analysis.template_rejected",
                    resource_type="data_catalog_example_query",
                    resource_id="none",
                    detail=json.dumps({
                        "conversation_id": conversation_id,
                        "query_length": len(payload.content),
                        "template_attempt": query_safety_reason,
                        "analysis_mode": "refusal",
                        "free_sql": False,
                    }, ensure_ascii=False),
                ))
                await audit_session.commit()
        else:
            async with request.app.state.database.session_factory() as analysis_session:
                user_role_codes = [role.code for role in user.roles]
                examples_statement = select(DataCatalogExampleQuery).where(
                    DataCatalogExampleQuery.is_active.is_(True),
                    DataCatalogExampleQuery.is_approved.is_(True),
                )
                if not user.is_super_admin:
                    examples_statement = examples_statement.where(
                        or_(*[DataCatalogExampleQuery.allowed_roles_json.contains([role]) for role in user_role_codes])
                    ) if user_role_codes else examples_statement.where(DataCatalogExampleQuery.id.is_(None))
                examples = list((await analysis_session.execute(examples_statement.limit(50))).scalars().all())
                ranked = sorted(examples, key=lambda row: analyzer.template_match_score(row, payload.content), reverse=True)
                selected_template_score = analyzer.template_match_score(ranked[0], payload.content) if ranked else 0
                template = ranked[0] if ranked and selected_template_score > 0 else None
                catalog_cache = PermissionScopedCache(request.app.state.redis.client) if getattr(request.app.state, "redis", None) else None
                catalog_service = DataCatalogService(DataCatalogRepository(request.app.state.database), permission_cache=catalog_cache)
                catalog_context = await catalog_service.search(
                    # Search the approved template's semantic question, not the raw subject ID
                    # appended by an employee; this keeps directory retrieval relevant while
                    # subject authorization remains enforced by SubjectScopeService.
                    query=template.question if template is not None else payload.content,
                    scope=await CatalogSearchScope.from_user(user, request.app.state.redis.client),
                    top_k=5,
                )
                catalog_tables = list((await analysis_session.execute(select(DataCatalogTable).where(
                    DataCatalogTable.id.in_([item["id"] for item in catalog_context.get("tables", [])]),
                    DataCatalogTable.is_active.is_(True),
                    DataCatalogTable.is_queryable.is_(True),
                ))).scalars().all()) if catalog_context.get("tables") else []
                catalog_columns = list((await analysis_session.execute(select(DataCatalogColumn).where(
                    DataCatalogColumn.table_id.in_([table.id for table in catalog_tables]),
                    DataCatalogColumn.is_queryable.is_(True),
                ))).scalars().all()) if catalog_tables else []
                if template is not None:
                    authorized_subject_ids = await SubjectScopeService.authorized_subject_ids(analysis_session, user)
                    template_attempt = await analyzer.analyze_template_attempt(
                        analysis_session,
                        user=user,
                        template=template,
                        query=payload.content,
                        catalog_tables=catalog_tables,
                        catalog_columns=catalog_columns,
                        authorized_subject_ids=authorized_subject_ids,
                    )
                    analysis = template_attempt.result
                    template_attempt_reason = template_attempt.reason
                    template_attempt_id = template_attempt.template_id
                else:
                    template_attempt_reason = "no_template_match"
                recoverable_parameter_reason = template_attempt_reason.removeprefix("invalid_parameters:") if template_attempt_reason.startswith("invalid_parameters:") else ""
                if analysis is None and not analyzer.clarification_for_parameter_reason(recoverable_parameter_reason, template=template):
                    analysis = await analyzer.analyze(analysis_session, user=user, query=payload.content)
                audit_detail = json.dumps({
                    "conversation_id": conversation_id,
                    "query_length": len(payload.content),
                    "template_id": template_attempt_id,
                    "template_attempt": template_attempt_reason,
                    "selected_template_id": str(template.id) if template is not None else None,
                    "selected_template_score": selected_template_score,
                    "candidate_template_count": len(examples),
                    "analysis_mode": analysis.query_plan.get("mode") if analysis else "fallback",
                    "free_sql": False,
                    "catalog_retrieval_mode": catalog_context.get("retrieval_mode"),
                    "catalog_table_ids": [item["id"] for item in catalog_context.get("tables", [])],
                    "catalog_column_count": len(catalog_context.get("columns", [])),
                }, ensure_ascii=False)
                analysis_session.add(AuditLog(
                    id=str(uuid4()),
                    actor_user_id=user.id,
                    action="data_analysis.template_executed" if template_attempt_reason == "executed" else "data_analysis.template_rejected",
                    resource_type="data_catalog_example_query",
                    resource_id=template_attempt_id or "none",
                    detail=audit_detail,
                ))
                await analysis_session.commit()
    if analysis_refusal_reason is not None:
        result = CustomerTaskExecutionResponse(
            conversation_id=conversation_id,
            overall_status="refused",
            answer="出于数据安全要求，不能执行或解释用户提供的 SQL。请使用自然语言描述分析需求。",
            route="data_analysis",
            agents=["DataAnalysisAgent"],
            results=[TaskExecutionResultResponse(task_id="analysis-refused", intent="data_analysis", status="refused", answer="请求已被安全策略拒绝。", data={"mode": "refusal", "reason": analysis_refusal_reason, "free_sql": False}, error=analysis_refusal_reason)],
        )
    elif analysis is not None:
        result = CustomerTaskExecutionResponse(
            conversation_id=conversation_id,
            overall_status="completed",
            answer=f"{analysis.title}：{analysis.value:,.2f}" if isinstance(analysis.value, float) else f"{analysis.title}：{analysis.value}",
            route="data_analysis",
            agents=["DataAnalysisAgent"],
            results=[TaskExecutionResultResponse(task_id="analysis-1", intent="data_analysis", status="succeeded", answer=analysis.explanation, data={"metric_code": analysis.metric_code, "value": analysis.value, "unit": analysis.unit, "query_plan": analysis.query_plan, "chart_spec": analysis.chart_spec})],
        )
    else:
        parameter_reason = template_attempt_reason.removeprefix("invalid_parameters:") if template_attempt_reason.startswith("invalid_parameters:") else ""
        clarification = analyzer.clarification_for_parameter_reason(parameter_reason, template=template) if parameter_reason else None
        if clarification:
            result = CustomerTaskExecutionResponse(
                conversation_id=conversation_id,
                overall_status="needs_clarification",
                answer=f"下一步：{clarification}",
                route="data_analysis",
                agents=["DataAnalysisAgent"],
                clarification=clarification,
                results=[TaskExecutionResultResponse(
                    task_id="analysis-clarification",
                    intent="data_analysis",
                    status="needs_clarification",
                    answer="",
                    data={"mode": "approved_template", "free_sql": False, "reason": parameter_reason},
                    error=parameter_reason,
                    clarification=clarification,
                )],
            )
        else:
            result = await CustomerAgentExecutionService(request.app.state.database, request.app.state.redis.client, settings=request.app.state.settings).execute(customer_id=user.id, conversation_id=conversation_id, query=payload.content)
    selected = [agent for agent in result.agents if agent in available_agents]
    if not selected:
        if "CustomerServiceAgent" in available_agents:
            result.route = "customer_service"
            result.agents = ["CustomerServiceAgent"]
            result.answer = "当前身份不具备该领域助手权限，已由客服助手为您提供受控说明或下一步指引。"
            result.results = []
            result.clarification = "如需内部分析、投顾、风控或业务处理，请使用具备相应权限的工作账号。"
        else:
            result.answer = "当前账号没有可用的智能助手权限。"
            result.results = []
            result.clarification = "请联系管理员确认账号角色和权限。"
    else:
        result.agents = selected
    result.available_agents = available_agents
    result.denied_agents = denied_agents
    result.page_context = payload.page_context
    return ApiResponse(data=result, trace_id=get_trace_id())


@router.post("/execute-plan", response_model=ApiResponse[CustomerTaskExecutionResponse])
async def execute_plan(request: Request, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    conversation_id = request.headers.get("X-Conversation-Id") or str(uuid4())
    result = await CustomerAgentExecutionService(request.app.state.database, request.app.state.redis.client, settings=request.app.state.settings).execute(customer_id=user.id, conversation_id=conversation_id, query=payload.note)
    result.route = "customer_service"
    result.agents = ["CustomerServiceAgent"]
    return ApiResponse(data=result, trace_id=get_trace_id())


@router.websocket("/ws/{conversation_id}")
async def customer_agent_websocket(websocket: WebSocket, conversation_id: str):
    user = await websocket_current_user(websocket)
    if user is None or not _is_customer(user):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    sequence = 0

    async def emit(event_type: str, request_id: str, payload: dict | None = None):
        nonlocal sequence
        sequence += 1
        await websocket.send_json(AgentStreamEvent(type=event_type, conversation_id=conversation_id, request_id=request_id, sequence=sequence, payload=payload or {}).as_dict())

    async def relay_customer_event():
        pubsub = websocket.app.state.redis.client.pubsub()
        channel = f"customer-realtime:{user.id}"
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    await emit(event.get("type", "human.session.updated"), "", {"session_id": event.get("session_id"), **(event.get("payload") or {})})
                except (WebSocketDisconnect, RuntimeError):
                    return
        finally:
            await pubsub.close()

    active_requests: dict[str, asyncio.Task] = {}

    async def run_request(request_id: str, content: str):
        await emit("agent.accepted", request_id, {"display": "已收到您的问题"})
        await emit("agent.progress", request_id, {"stage": "planning", "display": "正在识别问题并准备查询"})
        try:
            if content.startswith("[cancel-test]"):
                await asyncio.sleep(2)
            result = await CustomerAgentExecutionService(websocket.app.state.database, websocket.app.state.redis.client, settings=websocket.app.state.settings).execute(customer_id=user.id, conversation_id=conversation_id, query=content)
            await emit("agent.progress", request_id, {"stage": "rendering", "display": "正在整理回复"})
            for chunk in display_chunks(result.answer):
                await emit("agent.output.chunk", request_id, {"content": chunk})
            for task in result.results:
                await emit("agent.task.completed", request_id, {"task": task.model_dump()})
            await emit("agent.final", request_id, {"result": result.model_dump()})
        except asyncio.CancelledError:
            await asyncio.shield(emit("agent.cancelled", request_id, {"display": "本次请求已取消"}))
            raise
        except Exception:
            await emit("agent.error", request_id, {"code": "AGENT_EXECUTION_FAILED", "message": "客服服务暂时不可用"})
        finally:
            active_requests.pop(request_id, None)

    realtime_task = asyncio.create_task(relay_customer_event())
    try:
        await emit("session.ready", "", {"customer_id": user.id})
        while True:
            incoming = await websocket.receive_json()
            event_type = incoming.get("type")
            request_id = str(incoming.get("request_id") or "")
            if event_type == "agent.cancel":
                task = active_requests.get(request_id)
                if task is None:
                    await emit("agent.error", request_id, {"code": "AGENT_REQUEST_NOT_FOUND", "message": "request is not active"})
                else:
                    task.cancel()
                continue
            if event_type == "agent.amend":
                event_type = "agent.request"
                request_id = request_id or str(uuid4())
            if event_type != "agent.request":
                await emit("agent.error", request_id, {"code": "AGENT_EVENT_UNSUPPORTED", "message": "unsupported event"})
                continue
            request_id = request_id or str(uuid4())
            content = str(incoming.get("content") or incoming.get("amendment") or "").strip()
            if not content:
                await emit("agent.error", request_id, {"code": "AGENT_REQUEST_EMPTY", "message": "content is required"})
                continue
            if request_id in active_requests:
                await emit("agent.error", request_id, {"code": "AGENT_REQUEST_DUPLICATE", "message": "request is already active"})
                continue
            active_requests[request_id] = asyncio.create_task(run_request(request_id, content))
    except WebSocketDisconnect:
        return
    finally:
        for task in active_requests.values():
            task.cancel()
        realtime_task.cancel()
        await asyncio.gather(*active_requests.values(), realtime_task, return_exceptions=True)

@router.post("/read-tool", response_model=ApiResponse[dict])
async def read_tool(request: Request, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    tool_name = request.headers.get("X-Tool-Name", "")
    result = await CustomerReadTools(request.app.state.database).execute(name=tool_name, customer_id=user.id, query=payload.note)
    return ApiResponse(data={"tool": result.name, "success": result.success, "answer": result.answer, "data": result.data, "error": result.error}, trace_id=get_trace_id())


@router.get("", response_model=ApiResponse[list[CustomerServiceSessionResponse]])
async def list_sessions(request: Request, status: str = Query("waiting_human"), user: Annotated[User, Depends(current_user)] = None):
    async with request.app.state.database.session_factory() as session:
        if _is_customer(user):
            query = select(CustomerServiceSession).where(CustomerServiceSession.customer_id == user.id)
        else:
            _require_agent(user)
            allowed = {"waiting_human", "paused", "human_active", "resolved", "closed", "expired"}
            if status not in allowed:
                raise HTTPException(status_code=422, detail={"code": "CUSTOMER_SERVICE_STATUS_INVALID", "message": "invalid session status"})
            query = select(CustomerServiceSession).where(CustomerServiceSession.status == status)
        rows = list((await session.execute(query.order_by(CustomerServiceSession.updated_at.desc()).limit(100))).scalars().all())
        return ApiResponse(data=[_response(row) for row in rows], trace_id=get_trace_id())


@router.post("", response_model=ApiResponse[CustomerServiceSessionResponse], status_code=201)
async def create_session(request: Request, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    if not _is_customer(user):
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_CUSTOMER_REQUIRED", "message": "only customers can create handoff sessions"})
    async with request.app.state.database.session_factory() as session:
        row = CustomerServiceSession(id=str(uuid4()), customer_id=user.id, status="waiting_human", mode="human", handoff_reason=payload.note.strip() or "customer_requested")
        session.add(row)
        _audit(session, user_id=user.id, action="customer_service_session_create", row=row, detail=payload.note.strip())
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.post("/{session_id}/claim", response_model=ApiResponse[CustomerServiceSessionResponse])
async def claim_session(request: Request, session_id: str, user: Annotated[User, Depends(current_user)]):
    _require_agent(user)
    async with request.app.state.database.session_factory() as db_session:
        row = await db_session.get(CustomerServiceSession, session_id, with_for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_SERVICE_SESSION_NOT_FOUND", "message": "session not found"})
        if row.status not in RECLAIMABLE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "session is not available for claim"})
        await _ensure_agent_free(db_session, user.id, exclude_session_id=row.id)
        previous = row.assigned_agent_id
        row.assigned_agent_id = user.id; row.status = "human_active"; row.mode = "human"; row.accepted_at = datetime.now(timezone.utc); row.ended_at = None
        await enqueue_customer_service_notification(db_session, user_id=row.customer_id, notification_type="customer_service_claimed", title="人工客服已接入", content="已有客服接入您的会话，您可以继续发送消息。", session_id=row.id, event_key=f"customer-service-claim:{row.id}:{row.customer_id}")
        _audit(db_session, user_id=user.id, action="customer_service_session_claim", row=row, detail=f"previous_agent={previous or ''}")
        try:
            await db_session.commit()
        except IntegrityError as exc:
            await db_session.rollback()
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_ALREADY_CLAIMED", "message": "session was claimed by another客服"}) from exc
        await db_session.refresh(row)
        await publish_customer_realtime(request.app.state.redis.client, row.customer_id, 'human.session.claimed', row.id, {'status': row.status, 'assigned_agent_id': row.assigned_agent_id})
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.post("/{session_id}/heartbeat", response_model=ApiResponse[CustomerServiceSessionResponse])
async def heartbeat_session(request: Request, session_id: str, user: Annotated[User, Depends(current_user)]):
    _require_agent(user)
    async with request.app.state.database.session_factory() as session:
        row = await session.get(CustomerServiceSession, session_id, with_for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_SERVICE_SESSION_NOT_FOUND", "message": "session not found"})
        if row.status != "human_active" or row.assigned_agent_id != user.id:
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_HEARTBEAT_NOT_ACTIVE", "message": "only the active assigned agent can heartbeat"})
        row.agent_last_heartbeat_at = datetime.now(timezone.utc)
        _audit(session, user_id=user.id, action="customer_service_session_heartbeat", row=row)
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.post("/sweep-timeouts", response_model=ApiResponse[dict])
async def sweep_timeouts(request: Request, timeout_seconds: int = Query(90, ge=30, le=3600), user: Annotated[User, Depends(current_user)] = None):
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_SWEEP_REQUIRED", "message": "super admin required"})
    cutoff = datetime.now(timezone.utc).timestamp() - timeout_seconds
    changed = []
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(select(CustomerServiceSession).where(CustomerServiceSession.status == "human_active", CustomerServiceSession.agent_last_heartbeat_at.is_not(None)))).scalars().all())
        now = datetime.now(timezone.utc)
        for row in rows:
            heartbeat = row.agent_last_heartbeat_at.timestamp() if row.agent_last_heartbeat_at else 0
            if heartbeat < cutoff:
                row.status = "paused"; row.assigned_agent_id = None; row.agent_last_heartbeat_at = None
                _audit(session, user_id=user.id, action="customer_service_session_timeout_pause", row=row, detail=f"timeout_seconds={timeout_seconds}")
                changed.append(row.id)
        await session.commit()
    return ApiResponse(data={"paused_count": len(changed), "session_ids": changed, "timeout_seconds": timeout_seconds}, trace_id=get_trace_id())


@router.post("/{session_id}/assign", response_model=ApiResponse[CustomerServiceSessionResponse])
async def assign_session(request: Request, session_id: str, payload: CustomerServiceAssignRequest, user: Annotated[User, Depends(current_user)]):
    if not _is_supervisor(user):
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_ASSIGN_REQUIRED", "message": "supervisor assignment permission required"})
    async with request.app.state.database.session_factory() as db_session:
        row = await db_session.get(CustomerServiceSession, session_id, with_for_update=True)
        agent = await _get_agent(db_session, payload.assigned_agent_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_SERVICE_SESSION_NOT_FOUND", "message": "session not found"})
        if agent is None or not _is_agent(agent):
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_SERVICE_AGENT_NOT_FOUND", "message": "active customer service agent not found"})
        if row.status not in RECLAIMABLE_STATUSES:
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "session is not waiting or paused"})
        await _ensure_agent_free(db_session, agent.id, exclude_session_id=row.id)
        row.assigned_agent_id = agent.id; row.status = "human_active"; row.mode = "human"; row.accepted_at = datetime.now(timezone.utc); row.ended_at = None
        _audit(db_session, user_id=user.id, action="customer_service_session_assign", row=row, detail=f"assigned_agent={agent.id};note={payload.note.strip()}")
        try:
            await db_session.commit()
        except IntegrityError as exc:
            await db_session.rollback()
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_ASSIGN_CONFLICT", "message": "agent or session was assigned concurrently"}) from exc
        await db_session.refresh(row)
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


async def _owned_active_session(request: Request, db_session, session_id: str, user: User) -> CustomerServiceSession:
    row = await db_session.get(CustomerServiceSession, session_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_SERVICE_SESSION_NOT_FOUND", "message": "session not found"})
    if _is_customer(user):
        if row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_SESSION_SCOPE", "message": "session is outside customer scope"})
    elif row.assigned_agent_id != user.id and not _is_supervisor(user):
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_SESSION_ASSIGNEE_REQUIRED", "message": "only assigned agent can operate this session"})
    return row


@router.post("/{session_id}/pause", response_model=ApiResponse[CustomerServiceSessionResponse])
async def pause_session(request: Request, session_id: str, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    _require_agent(user)
    async with request.app.state.database.session_factory() as session:
        row = await _owned_active_session(request, session, session_id, user)
        if row.status != "human_active":
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "only active sessions can be paused"})
        row.status = "paused"
        _audit(session, user_id=user.id, action="customer_service_session_pause", row=row, detail=payload.note.strip())
        await session.commit(); await session.refresh(row)
        await publish_customer_realtime(request.app.state.redis.client, row.customer_id, 'human.session.paused', row.id, {'status': 'paused', 'assigned_agent_id': row.assigned_agent_id})
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.post("/{session_id}/release", response_model=ApiResponse[CustomerServiceSessionResponse])
async def release_session(request: Request, session_id: str, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    _require_agent(user)
    async with request.app.state.database.session_factory() as session:
        row = await _owned_active_session(request, session, session_id, user)
        if row.status != "human_active":
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "only active sessions can be released"})
        row.status = "waiting_human"; row.assigned_agent_id = None; row.accepted_at = None
        _audit(session, user_id=user.id, action="customer_service_session_release", row=row, detail=payload.note.strip())
        await session.commit(); await session.refresh(row)
        await publish_customer_realtime(request.app.state.redis.client, row.customer_id, 'human.session.released', row.id, {'status': 'waiting_human', 'assigned_agent_id': row.assigned_agent_id})
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.post("/{session_id}/resolve", response_model=ApiResponse[CustomerServiceSessionResponse])
async def resolve_session(request: Request, session_id: str, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    _require_agent(user)
    if not payload.note.strip():
        raise HTTPException(status_code=422, detail={"code": "CUSTOMER_SERVICE_RESOLUTION_NOTE_REQUIRED", "message": "resolution note is required"})
    async with request.app.state.database.session_factory() as session:
        row = await _owned_active_session(request, session, session_id, user)
        if row.status not in {"human_active", "paused"}:
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "only active or paused sessions can be resolved"})
        row.status = "resolved"; row.ended_at = datetime.now(timezone.utc)
        _audit(session, user_id=user.id, action="customer_service_session_resolve", row=row, detail=payload.note.strip())
        await session.commit(); await session.refresh(row)
        await publish_customer_realtime(request.app.state.redis.client, row.customer_id, 'human.session.resolved', row.id, {'status': 'resolved', 'assigned_agent_id': row.assigned_agent_id})
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.post("/{session_id}/customer-close", response_model=ApiResponse[CustomerServiceSessionResponse])
async def customer_close_session(request: Request, session_id: str, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    if not _is_customer(user):
        raise HTTPException(status_code=403, detail={"code": "CUSTOMER_SERVICE_CUSTOMER_REQUIRED", "message": "only customer can close own session"})
    async with request.app.state.database.session_factory() as session:
        row = await _owned_active_session(request, session, session_id, user)
        if row.status not in {"waiting_human", "paused", "human_active"}:
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "session cannot be customer-closed"})
        row.status = "resolved"; row.ended_at = datetime.now(timezone.utc); row.assigned_agent_id = None if row.status != "human_active" else row.assigned_agent_id
        _audit(session, user_id=user.id, action="customer_service_session_customer_close", row=row, detail=payload.note.strip() or "customer_requested_close")
        await session.commit(); await session.refresh(row)
        await publish_customer_realtime(request.app.state.redis.client, row.customer_id, 'human.session.resolved', row.id, {'status': 'resolved', 'assigned_agent_id': row.assigned_agent_id})
        return ApiResponse(data=_response(row), trace_id=get_trace_id())


@router.post("/{session_id}/close", response_model=ApiResponse[CustomerServiceSessionResponse])
async def close_session(request: Request, session_id: str, user: Annotated[User, Depends(current_user)]):
    _require_agent(user)
    async with request.app.state.database.session_factory() as session:
        row = await _owned_active_session(request, session, session_id, user)
        if row.status != "resolved":
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_STATE_CONFLICT", "message": "only resolved sessions can be closed"})
        row.status = "closed"; row.assigned_agent_id = None
        _audit(session, user_id=user.id, action="customer_service_session_close", row=row)
        await session.commit(); await session.refresh(row)
        await publish_customer_realtime(request.app.state.redis.client, row.customer_id, 'human.session.closed', row.id, {'status': 'closed', 'assigned_agent_id': row.assigned_agent_id})
        return ApiResponse(data=_response(row), trace_id=get_trace_id())
