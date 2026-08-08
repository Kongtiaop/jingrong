import json
from datetime import datetime, timezone, timedelta
from typing import Annotated
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_permission
from app.models.audit import AuditLog
from app.services.notification_service import create_workorder_notification
from app.models.auth import User
from app.models.trading import AmlBatchRun, AmlSchedulerState, RiskAlert, Order
from app.models.workorder import WorkOrder
from app.models.workorder_update import WorkOrderUpdate
from app.models.profile import Product
from app.schemas.profile import AmlBatchRunListResponse, AmlBatchRunResponse, AmlSchedulerStatusResponse, RiskAlertHandleRequest, RiskAlertActionRequest, RiskAlertResponse

router = APIRouter(prefix="/risk-alerts", tags=["risk-alerts"])

def alert_response(row, customer_name="", order_no="", product_name="", handled_by_name="", work_order=None, latest_customer_supplement=None):
    return RiskAlertResponse(id=row.id, customer_id=row.customer_id, order_id=row.order_id, rule_code=row.rule_code, alert_level=row.alert_level, status=row.status, confidence=float(row.confidence or 0), evidence=json.loads(row.evidence_json or "{}"), recommendation=row.recommendation, handled_by=row.handled_by, handled_note=row.handled_note, created_at=row.created_at, handled_at=row.handled_at, assessment_result=row.assessment_result, assessment_note=row.assessment_note, customer_consulted=row.customer_consulted, customer_consultation_note=row.customer_consultation_note, disposition_code=row.disposition_code, disposition_note=row.disposition_note, action_code=row.action_code, action_status=row.action_status, action_note=row.action_note, investigation_id=row.investigation_id, report_draft_id=row.report_draft_id, restriction_status=row.restriction_status, restriction_expires_at=row.restriction_expires_at, customer_name=customer_name, order_no=order_no, product_name=product_name, handled_by_name=handled_by_name, work_order_id=work_order.id if work_order else None, work_order_ticket_no=work_order.ticket_no if work_order else "", work_order_status=work_order.status if work_order else "", latest_customer_supplement=latest_customer_supplement.content if latest_customer_supplement else "", latest_customer_supplement_at=latest_customer_supplement.created_at if latest_customer_supplement else None)


@router.post("/scan", response_model=ApiResponse[dict])
async def scan_risk_windows(request: Request, customer_id: str = Query(""), days: int = Query(5, ge=1, le=30), user: Annotated[User, Depends(require_permission("risk:scan_execute"))] = None):
    if not customer_id:
        raise HTTPException(status_code=400, detail={"code": "CUSTOMER_ID_REQUIRED", "message": "customer_id is required"})
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        from app.services.risk_alert_service import RiskAlertService
        alerts = await RiskAlertService(uow).scan_customer_window(session, customer_id, days=days)
        await session.commit()
        return ApiResponse(data={"created": len(alerts), "items": [alert_response(x).model_dump(mode="json") for x in alerts]})

@router.get("/batches", response_model=ApiResponse[AmlBatchRunListResponse])
async def list_aml_batches(request: Request, status: str = Query(""), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("risk:alert_read"))] = None):
    async with request.app.state.database.session_factory() as session:
        query = select(AmlBatchRun)
        count_query = select(__import__("sqlalchemy").func.count()).select_from(AmlBatchRun)
        if status:
            query = query.where(AmlBatchRun.status == status)
            count_query = count_query.where(AmlBatchRun.status == status)
        total = int((await session.execute(count_query)).scalar_one())
        rows = list((await session.execute(query.order_by(AmlBatchRun.started_at.desc()).offset(offset).limit(limit))).scalars().all())
        return ApiResponse(data=AmlBatchRunListResponse(items=[AmlBatchRunResponse.model_validate(row, from_attributes=True) for row in rows], total=total))


@router.get("/scheduler-status", response_model=ApiResponse[AmlSchedulerStatusResponse])
async def aml_scheduler_status(request: Request, user: Annotated[User, Depends(require_permission("risk:alert_read"))] = None):
    async with request.app.state.database.session_factory() as session:
        row = await session.get(AmlSchedulerState, "daily_aml")
        heartbeat = row.last_heartbeat_at if row else None
        stale_after = max(request.app.state.settings.aml_scheduler_poll_seconds * 3, 180)
        running = bool(heartbeat and heartbeat >= datetime.now(timezone.utc) - timedelta(seconds=stale_after))
        return ApiResponse(data=AmlSchedulerStatusResponse(running=running, last_heartbeat_at=heartbeat, last_scan_started_at=row.last_scan_started_at if row else None, last_scan_finished_at=row.last_scan_finished_at if row else None, last_scan_status=row.last_scan_status if row else "never", last_error=row.last_error if row else ""))


@router.get("/batches/{batch_id}/alerts", response_model=ApiResponse[dict])
async def list_aml_batch_alerts(request: Request, batch_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("risk:alert_read"))] = None):
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        batch = (await session.execute(select(AmlBatchRun).where(AmlBatchRun.id == batch_id))).scalar_one_or_none()
        if batch is None:
            raise HTTPException(status_code=404, detail={"code": "AML_BATCH_NOT_FOUND", "message": "AML batch not found"})
        rows = list((await session.execute(select(RiskAlert).where(
            RiskAlert.order_id.is_(None),
            RiskAlert.evidence_json.like('%"scan_type": "customer_window"%'),
            RiskAlert.evidence_json.like(f'%"window_start": "{batch.scan_window_start.isoformat()}"%'),
            RiskAlert.evidence_json.like(f'%"window_end": "{batch.scan_window_end.isoformat()}"%'),
        ).order_by(RiskAlert.created_at.desc()).offset(offset).limit(limit))).scalars().all())
        user_ids = {row.customer_id for row in rows}
        users = {row.id: row for row in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()} if user_ids else {}
        items = [alert_response(row, users.get(row.customer_id).display_name if users.get(row.customer_id) else "").model_dump(mode="json") for row in rows]
        return ApiResponse(data={"batch": AmlBatchRunResponse.model_validate(batch, from_attributes=True).model_dump(mode="json"), "items": items, "total": len(items)})


@router.post("/batches/{batch_id}/retry", response_model=ApiResponse[dict])
async def retry_aml_batch(request: Request, batch_id: str, user: Annotated[User, Depends(require_permission("risk:scan_execute"))]):
    """Create scoped replacement scans for customers that failed in the source batch."""
    from app.repositories.unit_of_work import create_postgres_unit_of_work
    from app.services.daily_aml_scan_service import DailyAmlScanService

    async with request.app.state.database.session_factory() as session:
        batch = await session.get(AmlBatchRun, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail={"code": "AML_BATCH_NOT_FOUND", "message": "AML batch not found"})
        if batch.status not in {"failed", "completed_with_errors"}:
            raise HTTPException(status_code=409, detail={"code": "AML_BATCH_RETRY_NOT_ALLOWED", "message": "only failed AML batches can be retried"})
        try:
            failures = json.loads(batch.error_summary or "[]")
        except json.JSONDecodeError:
            failures = []
        usernames = sorted({item.get("username", "") for item in failures if item.get("username")})
        if not usernames:
            raise HTTPException(status_code=409, detail={"code": "AML_BATCH_RETRY_SCOPE_EMPTY", "message": "source batch has no retryable customer failures"})
        service = DailyAmlScanService(create_postgres_unit_of_work())
        runs = []
        for username in usernames:
            run, created = await service.run(session, as_of=batch.scan_window_end, days=batch.scan_days, username=username)
            runs.append({"username": username, "batch_id": run.id, "status": run.status, "created": created})
        await session.commit()
        return ApiResponse(data={"source_batch_id": batch.id, "retry_runs": runs})


@router.post("/batches/{batch_id}/late-rescan", response_model=ApiResponse[dict])
async def late_rescan_aml_batch(request: Request, batch_id: str, user: Annotated[User, Depends(require_permission("risk:scan_execute"))]):
    """Rescan an already completed window when late-arriving money flows are ingested."""
    from app.repositories.unit_of_work import create_postgres_unit_of_work
    from app.services.daily_aml_scan_service import DailyAmlScanService

    async with request.app.state.database.session_factory() as session:
        source = await session.get(AmlBatchRun, batch_id)
        if source is None:
            raise HTTPException(status_code=404, detail={"code": "AML_BATCH_NOT_FOUND", "message": "AML batch not found"})
        if source.status not in {"completed", "completed_with_errors"}:
            raise HTTPException(status_code=409, detail={"code": "AML_LATE_RESCAN_NOT_ALLOWED", "message": "only completed AML windows can be late-rescanned"})
        as_of = source.scan_window_end - timedelta(seconds=1)
        run, created = await DailyAmlScanService(create_postgres_unit_of_work()).run(session, as_of=as_of, days=source.scan_days, key_suffix=f"late_rescan:{source.id}", run_type="late_rescan", source_batch_id=source.id)
        await session.commit()
        return ApiResponse(data={"source_batch_id": source.id, "batch_id": run.id, "created": created, "status": run.status, "run_type": run.run_type})


@router.get("", response_model=ApiResponse[dict])
async def list_risk_alerts(request: Request, status: str = Query(""), alert_level: str = Query(""), customer_id: str = Query(""), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("risk:alert_read"))] = None):
    uow = request.app.state.trading_service.unit_of_work
    if not user.is_super_admin and not customer_id:
        customer_id = user.id
    async with request.app.state.database.session_factory() as session:
        total, rows = await uow.risk_alerts.list(session, customer_id=customer_id or None, status=status, alert_level=alert_level, limit=limit, offset=offset)
        user_ids={x for row in rows for x in [row.customer_id, row.handled_by] if x}; users={x.id:x for x in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()} if user_ids else {}
        order_ids={row.order_id for row in rows if row.order_id}; orders={x.id:x for x in (await session.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()} if order_ids else {}
        product_ids={x.product_id for x in orders.values()}; products={x.id:x for x in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()} if product_ids else {}
        alert_ids={row.id for row in rows}
        work_orders={item.risk_alert_id:item for item in (await session.execute(select(WorkOrder).where(WorkOrder.risk_alert_id.in_(alert_ids)))).scalars().all()} if alert_ids else {}
        work_order_ids={item.id for item in work_orders.values()}
        latest_supplements={}
        if work_order_ids:
            updates=list((await session.execute(select(WorkOrderUpdate).where(WorkOrderUpdate.work_order_id.in_(work_order_ids), WorkOrderUpdate.author_role == "customer").order_by(WorkOrderUpdate.work_order_id, WorkOrderUpdate.created_at.desc()))).scalars().all())
            for update in updates:
                latest_supplements.setdefault(update.work_order_id, update)
        items=[alert_response(row, users.get(row.customer_id).display_name if users.get(row.customer_id) else "", orders.get(row.order_id).order_no if orders.get(row.order_id) else "", products.get(orders.get(row.order_id).product_id).name if orders.get(row.order_id) and products.get(orders.get(row.order_id).product_id) else "", users.get(row.handled_by).display_name if row.handled_by and users.get(row.handled_by) else "", work_orders.get(row.id), latest_supplements.get(work_orders[row.id].id) if row.id in work_orders else None).model_dump(mode="json") for row in rows]
        return ApiResponse(data={"items": items, "total": total})

@router.get("/{alert_id}", response_model=ApiResponse[RiskAlertResponse])
async def get_risk_alert(request: Request, alert_id: str, user: Annotated[User, Depends(require_permission("risk:alert_read"))]):
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        row = await uow.risk_alerts.get_for_update(session, alert_id)
        if row is None or (not user.is_super_admin and row.customer_id != user.id):
            raise HTTPException(status_code=404, detail={"code": "RISK_ALERT_NOT_FOUND", "message": "risk alert not found"})
        return ApiResponse(data=alert_response(row))

@router.post("/{alert_id}/action", response_model=ApiResponse[RiskAlertResponse])
async def action_risk_alert(request: Request, alert_id: str, payload: RiskAlertActionRequest, user: Annotated[User, Depends(require_permission("risk:alert_action"))]):
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        row = await uow.risk_alerts.get_for_update(session, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "RISK_ALERT_NOT_FOUND", "message": "risk alert not found"})
        try:
            from app.services.risk_alert_service import RiskAlertService
            risk_service = RiskAlertService(uow)
            await risk_service.apply_action(session, row, user, payload.action_code, payload.note)
            if payload.action_code == "REQUEST_MATERIALS":
                work_order, created = await risk_service.create_material_request_work_order(session, row, user, payload.note)
                if created:
                    await create_workorder_notification(
                        session,
                        user_id=row.customer_id,
                        notification_type="risk_material_request",
                        title=f"请补充风险核验资料：{work_order.ticket_no}",
                        content=f"{work_order.title}。{payload.note.strip() or '请进入我的工单提交说明及附件。'}",
                        work_order=work_order,
                    )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "RISK_ACTION_CONFLICT", "message": str(exc)}) from exc
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="risk_action", resource_type="risk_alert", resource_id=row.id, detail=payload.model_dump_json()))
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=alert_response(row))

@router.post("/{alert_id}/handle", response_model=ApiResponse[RiskAlertResponse])
async def handle_risk_alert(request: Request, alert_id: str, payload: RiskAlertHandleRequest, user: Annotated[User, Depends(require_permission("risk:alert_handle"))]):
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        row = await uow.risk_alerts.get_for_update(session, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "RISK_ALERT_NOT_FOUND", "message": "risk alert not found"})
        if row.status in {"resolved", "dismissed"}:
            raise HTTPException(status_code=409, detail={"code": "RISK_ALERT_STATE_CONFLICT", "message": "risk alert is already closed"})
        if payload.status == "pending_customer" and payload.assessment_result != "insufficient_information":
            raise HTTPException(status_code=422, detail={"code": "RISK_PENDING_CUSTOMER_REQUIRES_INSUFFICIENT_INFO", "message": "等待客户补充资料时，评估结论必须为信息不足"})
        if payload.status == "pending_customer" and not (payload.assessment_note.strip() and payload.disposition_note.strip()):
            raise HTTPException(status_code=422, detail={"code": "RISK_MATERIAL_REQUEST_NOTE_REQUIRED", "message": "要求客户补充资料时，必须填写评估说明和具体资料清单"})
        if payload.status == "resolved":
            if payload.assessment_result == "confirmed_risk":
                raise HTTPException(status_code=422, detail={"code": "RISK_CANNOT_CLOSE_CONFIRMED", "message": "确认风险必须先完成调查、报告或限制解除，不能直接结案"})
            if row.restriction_status == "restricted":
                raise HTTPException(status_code=422, detail={"code": "RISK_RESTRICTION_MUST_BE_RELEASED", "message": "当前预警仍限制交易。请先保存“评估后通过”或“误报”的复核结论，再在客户详情解除交易限制，最后回到这里结案"})
            if payload.assessment_result in {None, "insufficient_information"}:
                raise HTTPException(status_code=422, detail={"code": "RISK_ASSESSMENT_INCOMPLETE", "message": "结案前必须有明确评估结论"})
            if payload.assessment_result == "cleared_after_review" and (not payload.customer_consulted or not payload.customer_consultation_note.strip() or not payload.assessment_note.strip()):
                raise HTTPException(status_code=422, detail={"code": "REVIEW_EVIDENCE_REQUIRED", "message": "评估后通过必须记录评估依据、客户沟通和沟通内容"})
        row.status = payload.status
        row.handled_by = user.id
        row.handled_note = payload.note
        row.handled_at = datetime.now(timezone.utc)
        row.assessment_result = payload.assessment_result
        row.assessment_note = payload.assessment_note
        row.customer_consulted = payload.customer_consulted
        row.customer_consultation_note = payload.customer_consultation_note
        row.disposition_code = payload.disposition_code
        row.disposition_note = payload.disposition_note
        if payload.assessment_result == "cleared_after_review" and (not payload.customer_consulted or not payload.customer_consultation_note.strip() or not payload.assessment_note.strip()):
            raise HTTPException(status_code=422, detail={"code": "REVIEW_EVIDENCE_REQUIRED", "message": "评估后通过必须记录评估依据、客户沟通和沟通内容"})
        if payload.assessment_result == "confirmed_risk" and payload.disposition_code in {None, "NO_ACTION"}:
            raise HTTPException(status_code=422, detail={"code": "DISPOSITION_REQUIRED", "message": "确认风险必须选择实际处置动作"})
        if payload.assessment_result in {"false_positive", "insufficient_information"} and not payload.assessment_note.strip():
            raise HTTPException(status_code=422, detail={"code": "ASSESSMENT_NOTE_REQUIRED", "message": "误报或信息不足必须填写评估说明"})
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="handle", resource_type="risk_alert", resource_id=row.id, detail=payload.model_dump_json()))
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=alert_response(row))
