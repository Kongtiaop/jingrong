from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.models.audit import AuditLog
from app.models.auth import Role, User
from app.models.customer_service import CustomerServiceMessage, CustomerServiceSession
from app.models.workorder import WorkOrder
from app.schemas.customer_service import CustomerIntentPlanResponse, IntentTaskResponse, TaskExecutionResultResponse, CustomerTaskExecutionResponse, CustomerServiceActionRequest, CustomerServiceAssignRequest, CustomerServiceMessageCreateRequest, CustomerServiceMessageResponse, CustomerServiceSessionResponse
from app.services.customer_intent import CustomerIntentPlanner
from app.services.memory_service import CustomerMemoryService
from app.services.customer_tools import CustomerReadTools
from app.services.customer_task_executor import CustomerTaskExecutor
from app.services.customer_rag_handoff import CustomerRagHandoffService

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


@router.get("/{session_id}/messages", response_model=ApiResponse[list[CustomerServiceMessageResponse]])
async def list_messages(request: Request, session_id: str, user: Annotated[User, Depends(current_user)], after: str | None = Query(None)):
    async with request.app.state.database.session_factory() as session:
        await _session_for_message(session, session_id, user)
        query = select(CustomerServiceMessage).where(CustomerServiceMessage.session_id == session_id)
        if after:
            query = query.where(CustomerServiceMessage.id > after)
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
        row.last_message_at = datetime.now(timezone.utc)
        _audit(session, user_id=user.id, action="customer_service_message_create", row=row, detail=json.dumps({"sender_type": sender_type}, ensure_ascii=False))
        await session.commit(); await session.refresh(message)
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
            arguments=item.arguments, confidence=item.confidence, status=item.status,
        ) for item in plan.tasks],
        clarification=plan.clarification, requires_handoff=plan.requires_handoff,
    ), trace_id=get_trace_id())


@router.post("/execute-plan", response_model=ApiResponse[CustomerTaskExecutionResponse])
async def execute_plan(request: Request, payload: CustomerServiceActionRequest, user: Annotated[User, Depends(current_user)]):
    conversation_id = request.headers.get("X-Conversation-Id") or str(uuid4())
    planner = CustomerIntentPlanner()
    memory = CustomerMemoryService(request.app.state.database, request.app.state.redis.client)
    short_term = await memory.load_short_term(customer_id=user.id, conversation_id=conversation_id)
    active_handoff = short_term.get("handoff") or {}
    if active_handoff.get("customer_service_session_id"):
        async with request.app.state.database.session_factory() as gate_session:
            gate = await gate_session.get(CustomerServiceSession, active_handoff["customer_service_session_id"])
        if gate is not None and gate.status in {"waiting_human", "human_active", "paused"}:
            message = "已转人工客服，请在当前会话中等待客服回复。" if gate.status != "human_active" else "当前由人工客服接待，智能客服已暂停。"
            return ApiResponse(data=CustomerTaskExecutionResponse(conversation_id=conversation_id, overall_status="human_active" if gate.status == "human_active" else "waiting_human", answer=message, results=[], requires_handoff=True, work_order_id=active_handoff.get("work_order_id"), ticket_no=active_handoff.get("ticket_no"), customer_service_session_id=gate.id, customer_service_session_status=gate.status), trace_id=get_trace_id())
    user_query = payload.note.strip()
    clarification_count = int(short_term.get("clarification_count") or 0)
    pending = short_term.get("pending_intent_plan") or {}
    original_query = short_term.get("original_query") or user_query
    combined_query = f"{original_query} {user_query}".strip() if pending.get("tasks") and user_query != original_query else user_query
    plan = planner.plan(combined_query)
    if plan.clarification:
        next_count = clarification_count + 1
        if next_count >= 2:
            handoff = await CustomerRagHandoffService(request.app.state.database).handoff(customer_id=user.id, query=original_query, intent="unknown", evidence_status="clarification_exhausted", citations=[])
            answer = "为避免反复追问，已为您转接人工客服。"
            await memory.save_short_term(customer_id=user.id, conversation_id=conversation_id, state={**short_term, "conversation_id": conversation_id, "original_query": original_query, "latest_query": user_query, "clarification_count": next_count, "pending_intent_plan": {}, "clarification": None, "missing_parameter": False, "handoff": handoff})
            return ApiResponse(data=CustomerTaskExecutionResponse(conversation_id=conversation_id, overall_status="handoff", answer=answer, results=[], clarification=None, requires_handoff=True, work_order_id=handoff["work_order_id"], ticket_no=handoff["ticket_no"], customer_service_session_id=handoff["customer_service_session_id"], customer_service_session_status=handoff["customer_service_session_status"]), trace_id=get_trace_id())
        await memory.save_short_term(customer_id=user.id, conversation_id=conversation_id, state={**short_term, "conversation_id": conversation_id, "original_query": original_query, "latest_query": user_query, "clarification_count": next_count, "pending_intent_plan": {"tasks": [item.__dict__ for item in plan.tasks]}, "clarification": plan.clarification, "missing_parameter": True})
        return ApiResponse(data=CustomerTaskExecutionResponse(conversation_id=conversation_id, overall_status="needs_clarification", answer=plan.clarification, results=[], clarification=plan.clarification, requires_handoff=plan.requires_handoff), trace_id=get_trace_id())
    results = await CustomerTaskExecutor(request.app.state.database).execute(customer_id=user.id, plan=plan)
    overall, clarification, aggregate_answer = CustomerTaskExecutor.summarize(results, requires_handoff=plan.requires_handoff)
    answer_lines = [aggregate_answer] if aggregate_answer else []
    needs_handoff = plan.requires_handoff or any(item.error == "low_confidence" for item in results)
    handoff = None
    if needs_handoff:
        handoff = await CustomerRagHandoffService(request.app.state.database).handoff(customer_id=user.id, query=payload.note.strip(), intent=next((item.intent for item in plan.tasks if item.intent != "human_handoff"), "human_handoff"), evidence_status="low_confidence" if any(item.error == "low_confidence" for item in results) else "customer_requested", citations=[])
        answer_lines.append(f"已为您转接人工客服，工单号：{handoff['ticket_no']}。")
    state = {**short_term, "conversation_id": conversation_id, "original_query": short_term.get("original_query") or payload.note.strip(), "latest_query": payload.note.strip(), "task_results": [item.__dict__ for item in results], "clarification": clarification, "missing_parameter": bool(clarification), "clarification_count": clarification_count, "pending_intent_plan": {}, "handoff": handoff}
    await memory.save_short_term(customer_id=user.id, conversation_id=conversation_id, state=state)
    return ApiResponse(data=CustomerTaskExecutionResponse(conversation_id=conversation_id, overall_status=overall, answer="\n".join(answer_lines) or "当前没有可返回的结果，建议转人工客服。", results=[TaskExecutionResultResponse(task_id=item.task_id, intent=item.intent, status=item.status, answer=item.answer, data=item.data or {}, error=item.error, clarification=item.clarification) for item in results], clarification=clarification, requires_handoff=needs_handoff, work_order_id=handoff["work_order_id"] if handoff else None, ticket_no=handoff["ticket_no"] if handoff else None, customer_service_session_id=handoff["customer_service_session_id"] if handoff else None, customer_service_session_status=handoff["customer_service_session_status"] if handoff else None), trace_id=get_trace_id())


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
        _audit(db_session, user_id=user.id, action="customer_service_session_claim", row=row, detail=f"previous_agent={previous or ''}")
        try:
            await db_session.commit()
        except IntegrityError as exc:
            await db_session.rollback()
            raise HTTPException(status_code=409, detail={"code": "CUSTOMER_SERVICE_SESSION_ALREADY_CLAIMED", "message": "session was claimed by another客服"}) from exc
        await db_session.refresh(row)
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
        return ApiResponse(data=_response(row), trace_id=get_trace_id())





