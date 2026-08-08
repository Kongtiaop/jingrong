from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Annotated
from uuid import uuid4
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload
from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.models.audit import AuditLog
from app.models.auth import Role, User
from app.models.workorder import WorkOrder
from app.models.trading import RiskAlert
from app.models.workorder_update import WorkOrderUpdate
from app.models.workorder_attachment import WorkOrderAttachment
from app.services.notification_service import create_workorder_notification
from app.schemas.workorder import WorkOrderAssignRequest, WorkOrderCreateRequest, WorkOrderCustomerSupplementRequest, WorkOrderHandleRequest, WorkOrderListResponse, WorkOrderResponse, WorkOrderTodoSummaryResponse, WorkOrderUpdateResponse
from app.schemas.workorder_attachment import WorkOrderAttachmentResponse

router = APIRouter(prefix="/workorders", tags=["workorders"])


def _permissions(user: User) -> set[str]:
    return {permission.code for role in user.roles for permission in role.permissions}


def _is_customer(user: User) -> bool:
    return any(role.code == "customer" for role in user.roles)


def _require(user: User, code: str) -> None:
    if user.is_super_admin:
        return
    if code not in _permissions(user):
        raise HTTPException(status_code=403, detail={"code": "WORKORDER_PERMISSION_REQUIRED", "message": f"{code} required"})


def _response(row: WorkOrder) -> WorkOrderResponse:
    return WorkOrderResponse.model_validate(row, from_attributes=True)


def _ticket_no() -> str:
    return f"WO{datetime.now(timezone.utc):%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}"


_ALLOWED_ATTACHMENT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}


def _upload_dir(request: Request) -> Path:
    target = Path(request.app.state.settings.workorder_upload_dir)
    if not target.is_absolute():
        target = Path.cwd() / target
    target.mkdir(parents=True, exist_ok=True)
    return target

def _attachment_response(row: WorkOrderAttachment) -> WorkOrderAttachmentResponse:
    return WorkOrderAttachmentResponse.model_validate(row, from_attributes=True)

def _update_response(row: WorkOrderUpdate) -> WorkOrderUpdateResponse:
    return WorkOrderUpdateResponse.model_validate(row, from_attributes=True)

_ALLOWED_HANDLE_STATUSES = {
    "open": {"in_progress", "pending_customer", "resolved"},
    "assigned": {"in_progress", "pending_customer", "resolved"},
    "in_progress": {"in_progress", "pending_customer", "resolved"},
    "pending_customer": {"in_progress", "pending_customer", "resolved"},
}


@router.post("", response_model=ApiResponse[WorkOrderResponse], status_code=201)
async def create_work_order(request: Request, payload: WorkOrderCreateRequest, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:create")
    customer_id = payload.customer_id
    if _is_customer(user):
        if customer_id and customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_SCOPE", "message": "customer can only create own work order"})
        customer_id = user.id
    elif not customer_id and not user.is_super_admin:
        raise HTTPException(status_code=422, detail={"code": "CUSTOMER_ID_REQUIRED", "message": "customer_id is required for staff work order"})
    async with request.app.state.database.session_factory() as session:
        if customer_id:
            customer = (await session.execute(select(User).options(selectinload(User.roles)).where(User.id == customer_id, User.status == "active"))).scalar_one_or_none()
            if customer is None or not _is_customer(customer):
                raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
        row = WorkOrder(id=str(uuid4()), ticket_no=_ticket_no(), customer_id=customer_id, created_by=user.id, category=payload.category, priority=payload.priority, status="open", title=payload.title, description=payload.description)
        session.add(row)
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="create", resource_type="work_order", resource_id=row.id, detail=payload.model_dump_json()))
        await session.commit()
        await session.refresh(row)
        return ApiResponse(data=_response(row))


def _apply_work_order_scope(query, user: User, scope: str):
    if _is_customer(user):
        return query.where(WorkOrder.customer_id == user.id)
    if scope == "assigned":
        return query.where(WorkOrder.assigned_to == user.id)
    return query


@router.get("", response_model=ApiResponse[WorkOrderListResponse])
async def list_work_orders(
    request: Request,
    status: str = Query("", pattern="^(|open|assigned|in_progress|pending_customer|resolved|closed|cancelled)$"),
    category: str = Query("", pattern="^(|customer_service|complaint|risk_investigation|business_exception)$"),
    priority: str = Query("", pattern="^(|low|normal|high|urgent)$"),
    scope: str = Query("all", pattern="^(all|assigned)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Annotated[User, Depends(current_user)] = None,
):
    _require(user, "workorder:read")
    async with request.app.state.database.session_factory() as session:
        query = _apply_work_order_scope(select(WorkOrder), user, scope)
        count_query = _apply_work_order_scope(select(func.count()).select_from(WorkOrder), user, scope)
        if status:
            query = query.where(WorkOrder.status == status)
            count_query = count_query.where(WorkOrder.status == status)
        if category:
            query = query.where(WorkOrder.category == category)
            count_query = count_query.where(WorkOrder.category == category)
        if priority:
            query = query.where(WorkOrder.priority == priority)
            count_query = count_query.where(WorkOrder.priority == priority)
        priority_rank = case((WorkOrder.priority == "urgent", 0), (WorkOrder.priority == "high", 1), (WorkOrder.priority == "normal", 2), else_=3)
        total = int((await session.execute(count_query)).scalar_one())
        rows = list((await session.execute(query.order_by(priority_rank, WorkOrder.created_at.desc()).offset(offset).limit(limit))).scalars().all())
        return ApiResponse(data=WorkOrderListResponse(items=[_response(row) for row in rows], total=total))


@router.get("/summary", response_model=ApiResponse[WorkOrderTodoSummaryResponse])
async def work_order_summary(request: Request, scope: str = Query("assigned", pattern="^(all|assigned)$"), user: Annotated[User, Depends(current_user)] = None):
    _require(user, "workorder:read")
    async with request.app.state.database.session_factory() as session:
        query = _apply_work_order_scope(select(WorkOrder.status, WorkOrder.priority), user, scope)
        rows = list((await session.execute(query)).all())
        status_counts = {status: 0 for status in ("open", "assigned", "in_progress", "pending_customer", "resolved")}
        priority_counts = {priority: 0 for priority in ("urgent", "high")}
        for status_value, priority_value in rows:
            if status_value in status_counts:
                status_counts[status_value] += 1
            if priority_value in priority_counts and status_value not in {"closed", "cancelled"}:
                priority_counts[priority_value] += 1
        return ApiResponse(data=WorkOrderTodoSummaryResponse(total=len(rows), urgent=priority_counts["urgent"], high=priority_counts["high"], **status_counts))

@router.get("/assignees", response_model=ApiResponse[list[dict]])
async def list_work_order_assignees(request: Request, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:assign")
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.status == "active").order_by(User.display_name, User.username))).scalars().all())
        items = []
        for row in rows:
            if _is_customer(row):
                continue
            role_permissions = {permission.code for role in row.roles for permission in role.permissions}
            if not row.is_super_admin and "workorder:handle" not in role_permissions:
                continue
            items.append({"id": row.id, "username": row.username, "display_name": row.display_name, "roles": [role.code for role in row.roles if role.code != "customer"]})
        return ApiResponse(data=items)


@router.get("/{work_order_id}", response_model=ApiResponse[WorkOrderResponse])
async def get_work_order(request: Request, work_order_id: str, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:read")
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if _is_customer(user) and row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_SCOPE", "message": "work order is outside customer scope"})
        return ApiResponse(data=_response(row))



@router.get("/{work_order_id}/updates", response_model=ApiResponse[list[WorkOrderUpdateResponse]])
async def list_work_order_updates(request: Request, work_order_id: str, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:read")
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if _is_customer(user) and row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_SCOPE", "message": "work order is outside customer scope"})
        updates = list((await session.execute(select(WorkOrderUpdate).where(WorkOrderUpdate.work_order_id == row.id).order_by(WorkOrderUpdate.created_at.asc()))).scalars().all())
        return ApiResponse(data=[_update_response(item) for item in updates])


@router.post("/{work_order_id}/supplements", response_model=ApiResponse[WorkOrderUpdateResponse], status_code=201)
async def submit_customer_supplement(request: Request, work_order_id: str, payload: WorkOrderCustomerSupplementRequest, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:read")
    if not _is_customer(user):
        raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_ONLY", "message": "only the work order customer can submit a supplement"})
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id, with_for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_SCOPE", "message": "work order is outside customer scope"})
        if row.status != "pending_customer":
            raise HTTPException(status_code=409, detail={"code": "WORKORDER_STATE_CONFLICT", "message": "customer supplements are accepted only while waiting for customer input"})
        update = WorkOrderUpdate(id=str(uuid4()), work_order_id=row.id, author_user_id=user.id, author_role="customer", content=payload.content.strip())
        session.add(update)
        row.status = "in_progress"
        if row.risk_alert_id:
            alert = await session.get(RiskAlert, row.risk_alert_id, with_for_update=True)
            if alert is not None and alert.status == "pending_customer":
                alert.status = "investigating"
                alert.handled_note = "客户已提交补充资料，等待风控复核。"
                alert.handled_at = datetime.now(timezone.utc)
                session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="customer_material_submitted", resource_type="risk_alert", resource_id=alert.id, detail=json.dumps({"work_order_id": row.id})))
        if row.assigned_to:
            await create_workorder_notification(
                session,
                user_id=row.assigned_to,
                notification_type="workorder_customer_supplemented",
                title=f"客户已补充工单：{row.ticket_no}",
                content=f"{row.title} 收到客户补充内容，请进入工单工作台查看并继续处理。",
                work_order=row,
            )
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="customer_supplement", resource_type="work_order", resource_id=row.id, detail=payload.model_dump_json()))
        await session.commit()
        await session.refresh(update)
        return ApiResponse(data=_update_response(update))
@router.post("/{work_order_id}/assign", response_model=ApiResponse[WorkOrderResponse])
async def assign_work_order(request: Request, work_order_id: str, payload: WorkOrderAssignRequest, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:assign")
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id, with_for_update=True)
        assignee = (await session.execute(select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == payload.assigned_to, User.status == "active"))).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if assignee is None or _is_customer(assignee) or (not assignee.is_super_admin and "workorder:handle" not in _permissions(assignee)):
            raise HTTPException(status_code=404, detail={"code": "ASSIGNEE_NOT_FOUND", "message": "active staff assignee not found"})
        if row.status in {"closed", "cancelled"}:
            raise HTTPException(status_code=409, detail={"code": "WORKORDER_STATE_CONFLICT", "message": "closed work order cannot be assigned"})
        row.assigned_to = assignee.id
        row.status = "assigned"
        await create_workorder_notification(
            session,
            user_id=assignee.id,
            notification_type="workorder_assigned",
            title=f"你收到新工单：{row.ticket_no}",
            content=f"{row.title}（优先级：{row.priority}）已分派给你，请进入工单工作台处理。",
            work_order=row,
        )
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="assign", resource_type="work_order", resource_id=row.id, detail=json.dumps({"assigned_to": assignee.id})))
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=_response(row))


@router.post("/{work_order_id}/handle", response_model=ApiResponse[WorkOrderResponse])
async def handle_work_order(request: Request, work_order_id: str, payload: WorkOrderHandleRequest, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:handle")
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id, with_for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if row.status in {"closed", "cancelled"} or payload.status not in _ALLOWED_HANDLE_STATUSES.get(row.status, set()):
            raise HTTPException(status_code=409, detail={"code": "WORKORDER_STATE_CONFLICT", "message": f"cannot move work order from {row.status} to {payload.status}"})
        if not user.is_super_admin and row.assigned_to != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_ASSIGNEE_REQUIRED", "message": "only the assigned staff member can handle this work order"})
        if payload.status in {"pending_customer", "resolved"} and not payload.note.strip():
            message = "resolution note is required when resolving a work order" if payload.status == "resolved" else "customer follow-up note is required when waiting for customer input"
            raise HTTPException(status_code=422, detail={"code": "WORKORDER_FEEDBACK_NOTE_REQUIRED", "message": message})
        if payload.status == "resolved":
            row.resolved_at = datetime.now(timezone.utc)
            row.resolution_note = payload.note.strip()
        if payload.status == "pending_customer":
            session.add(WorkOrderUpdate(id=str(uuid4()), work_order_id=row.id, author_user_id=user.id, author_role="staff", content=payload.note.strip()))
        row.status = payload.status
        if payload.status in {"pending_customer", "resolved"}:
            recipient_id = row.customer_id or row.created_by
            if recipient_id and recipient_id != user.id:
                status_text = "待你补充信息" if payload.status == "pending_customer" else "已解决"
                await create_workorder_notification(
                    session,
                    user_id=recipient_id,
                    notification_type=f"workorder_{payload.status}",
                    title=f"工单进度更新：{row.ticket_no}",
                    content=f"{row.title} 当前状态：{status_text}。处理说明：{payload.note.strip() or '暂无补充说明'}",
                    work_order=row,
                )
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="handle", resource_type="work_order", resource_id=row.id, detail=payload.model_dump_json()))
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=_response(row))


@router.post("/{work_order_id}/close", response_model=ApiResponse[WorkOrderResponse])
async def close_work_order(request: Request, work_order_id: str, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:handle")
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id, with_for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if row.status != "resolved":
            raise HTTPException(status_code=409, detail={"code": "WORKORDER_STATE_CONFLICT", "message": "only resolved work order can be closed"})
        if not user.is_super_admin and row.assigned_to != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_ASSIGNEE_REQUIRED", "message": "only the assigned staff member can close this work order"})
        row.status = "closed"; row.closed_at = datetime.now(timezone.utc)
        recipient_id = row.customer_id or row.created_by
        if recipient_id and recipient_id != user.id:
            await create_workorder_notification(
                session,
                user_id=recipient_id,
                notification_type="workorder_closed",
                title=f"工单已关闭：{row.ticket_no}",
                content=f"{row.title} 已关闭。如问题仍未解决，请重新提交工单。",
                work_order=row,
            )
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="close", resource_type="work_order", resource_id=row.id, detail="work order closed"))
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=_response(row))


















@router.get("/{work_order_id}/attachments", response_model=ApiResponse[list[WorkOrderAttachmentResponse]])
async def list_work_order_attachments(request: Request, work_order_id: str, user: Annotated[User, Depends(current_user)]):
    _require(user, "workorder:read")
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if _is_customer(user) and row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_SCOPE", "message": "work order is outside customer scope"})
        items = list((await session.execute(select(WorkOrderAttachment).where(WorkOrderAttachment.work_order_id == row.id).order_by(WorkOrderAttachment.created_at.asc()))).scalars().all())
        return ApiResponse(data=[_attachment_response(item) for item in items])


@router.post("/{work_order_id}/attachments", response_model=ApiResponse[WorkOrderAttachmentResponse], status_code=201)
async def upload_work_order_attachment(request: Request, work_order_id: str, file: UploadFile = File(...), user: Annotated[User, Depends(current_user)] = None):
    _require(user, "workorder:read")
    if not _is_customer(user):
        raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_ONLY", "message": "only the work order customer can upload an attachment"})
    content_type = (file.content_type or "").lower()
    extension = _ALLOWED_ATTACHMENT_TYPES.get(content_type)
    if extension is None:
        raise HTTPException(status_code=415, detail={"code": "WORKORDER_ATTACHMENT_TYPE", "message": "only JPG, PNG, and PDF attachments are allowed"})
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id, with_for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_NOT_FOUND", "message": "work order not found"})
        if row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_SCOPE", "message": "work order is outside customer scope"})
        if row.status != "pending_customer":
            raise HTTPException(status_code=409, detail={"code": "WORKORDER_STATE_CONFLICT", "message": "attachments are accepted only while waiting for customer input"})
        target = _upload_dir(request) / f"{uuid4().hex}{extension}"
        total = 0
        try:
            with target.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > request.app.state.settings.workorder_attachment_max_bytes:
                        raise HTTPException(status_code=413, detail={"code": "WORKORDER_ATTACHMENT_TOO_LARGE", "message": "attachment exceeds the configured size limit"})
                    output.write(chunk)
            if total == 0:
                raise HTTPException(status_code=422, detail={"code": "WORKORDER_ATTACHMENT_EMPTY", "message": "attachment cannot be empty"})
            item = WorkOrderAttachment(id=str(uuid4()), work_order_id=row.id, uploaded_by=user.id, original_name=Path(file.filename or "attachment").name, stored_name=target.name, content_type=content_type, size_bytes=total)
            session.add(item)
            session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="upload_attachment", resource_type="work_order", resource_id=row.id, detail=json.dumps({"attachment_id": item.id, "size_bytes": total})))
            await session.commit(); await session.refresh(item)
            return ApiResponse(data=_attachment_response(item))
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()


@router.get("/{work_order_id}/attachments/{attachment_id}/download")
async def download_work_order_attachment(request: Request, work_order_id: str, attachment_id: str, user: Annotated[User, Depends(current_user)]):
    from fastapi.responses import FileResponse
    _require(user, "workorder:read")
    async with request.app.state.database.session_factory() as session:
        row = await session.get(WorkOrder, work_order_id)
        item = await session.get(WorkOrderAttachment, attachment_id)
        if row is None or item is None or item.work_order_id != work_order_id:
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_ATTACHMENT_NOT_FOUND", "message": "attachment not found"})
        if _is_customer(user) and row.customer_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "WORKORDER_CUSTOMER_SCOPE", "message": "work order is outside customer scope"})
        path = _upload_dir(request) / item.stored_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "WORKORDER_ATTACHMENT_FILE_MISSING", "message": "attachment file is missing"})
        return FileResponse(path, media_type=item.content_type, filename=item.original_name)






