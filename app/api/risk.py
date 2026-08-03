import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_permission
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.trading import RiskAlert, Order
from app.models.profile import Product
from app.schemas.profile import RiskAlertHandleRequest, RiskAlertActionRequest, RiskAlertResponse

router = APIRouter(prefix="/risk-alerts", tags=["risk-alerts"])

def alert_response(row, customer_name="", order_no="", product_name="", handled_by_name=""):
    return RiskAlertResponse(id=row.id, customer_id=row.customer_id, order_id=row.order_id, rule_code=row.rule_code, alert_level=row.alert_level, status=row.status, confidence=float(row.confidence or 0), evidence=json.loads(row.evidence_json or "{}"), recommendation=row.recommendation, handled_by=row.handled_by, handled_note=row.handled_note, created_at=row.created_at, handled_at=row.handled_at, assessment_result=row.assessment_result, assessment_note=row.assessment_note, customer_consulted=row.customer_consulted, customer_consultation_note=row.customer_consultation_note, disposition_code=row.disposition_code, disposition_note=row.disposition_note, action_code=row.action_code, action_status=row.action_status, action_note=row.action_note, investigation_id=row.investigation_id, report_draft_id=row.report_draft_id, restriction_status=row.restriction_status, restriction_expires_at=row.restriction_expires_at, customer_name=customer_name, order_no=order_no, product_name=product_name, handled_by_name=handled_by_name)


@router.post("/scan", response_model=ApiResponse[dict])
async def scan_risk_windows(request: Request, customer_id: str = Query(""), days: int = Query(5, ge=1, le=30), user: Annotated[User, Depends(require_permission("risk:write"))] = None):
    if not customer_id:
        raise HTTPException(status_code=400, detail={"code": "CUSTOMER_ID_REQUIRED", "message": "customer_id is required"})
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        from app.services.risk_alert_service import RiskAlertService
        alerts = await RiskAlertService(uow).scan_customer_window(session, customer_id, days=days)
        await session.commit()
        return ApiResponse(data={"created": len(alerts), "items": [alert_response(x).model_dump(mode="json") for x in alerts]})

@router.get("", response_model=ApiResponse[dict])
async def list_risk_alerts(request: Request, status: str = Query(""), alert_level: str = Query(""), customer_id: str = Query(""), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("audit:read"))] = None):
    uow = request.app.state.trading_service.unit_of_work
    if not user.is_super_admin and not customer_id:
        customer_id = user.id
    async with request.app.state.database.session_factory() as session:
        total, rows = await uow.risk_alerts.list(session, customer_id=customer_id or None, status=status, alert_level=alert_level, limit=limit, offset=offset)
        user_ids={x for row in rows for x in [row.customer_id, row.handled_by] if x}; users={x.id:x for x in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()} if user_ids else {}
        order_ids={row.order_id for row in rows if row.order_id}; orders={x.id:x for x in (await session.execute(select(Order).where(Order.id.in_(order_ids)))).scalars().all()} if order_ids else {}
        product_ids={x.product_id for x in orders.values()}; products={x.id:x for x in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()} if product_ids else {}
        items=[alert_response(row, users.get(row.customer_id).display_name if users.get(row.customer_id) else "", orders.get(row.order_id).order_no if orders.get(row.order_id) else "", products.get(orders.get(row.order_id).product_id).name if orders.get(row.order_id) and products.get(orders.get(row.order_id).product_id) else "", users.get(row.handled_by).display_name if row.handled_by and users.get(row.handled_by) else "").model_dump(mode="json") for row in rows]
        return ApiResponse(data={"items": items, "total": total})

@router.get("/{alert_id}", response_model=ApiResponse[RiskAlertResponse])
async def get_risk_alert(request: Request, alert_id: str, user: Annotated[User, Depends(require_permission("audit:read"))]):
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        row = await uow.risk_alerts.get_for_update(session, alert_id)
        if row is None or (not user.is_super_admin and row.customer_id != user.id):
            raise HTTPException(status_code=404, detail={"code": "RISK_ALERT_NOT_FOUND", "message": "risk alert not found"})
        return ApiResponse(data=alert_response(row))

@router.post("/{alert_id}/action", response_model=ApiResponse[RiskAlertResponse])
async def action_risk_alert(request: Request, alert_id: str, payload: RiskAlertActionRequest, user: Annotated[User, Depends(require_permission("risk:write"))]):
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        row = await uow.risk_alerts.get_for_update(session, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "RISK_ALERT_NOT_FOUND", "message": "risk alert not found"})
        try:
            from app.services.risk_alert_service import RiskAlertService
            await RiskAlertService(uow).apply_action(session, row, user, payload.action_code, payload.note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "RISK_ACTION_CONFLICT", "message": str(exc)}) from exc
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="risk_action", resource_type="risk_alert", resource_id=row.id, detail=payload.model_dump_json()))
        await session.commit(); await session.refresh(row)
        return ApiResponse(data=alert_response(row))

@router.post("/{alert_id}/handle", response_model=ApiResponse[RiskAlertResponse])
async def handle_risk_alert(request: Request, alert_id: str, payload: RiskAlertHandleRequest, user: Annotated[User, Depends(require_permission("audit:read"))]):
    uow = request.app.state.trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        row = await uow.risk_alerts.get_for_update(session, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "RISK_ALERT_NOT_FOUND", "message": "risk alert not found"})
        if row.status in {"resolved", "dismissed"}:
            raise HTTPException(status_code=409, detail={"code": "RISK_ALERT_STATE_CONFLICT", "message": "risk alert is already closed"})
        if payload.status == "resolved":
            if payload.assessment_result == "confirmed_risk":
                raise HTTPException(status_code=422, detail={"code": "RISK_CANNOT_CLOSE_CONFIRMED", "message": "确认风险必须先完成调查、报告或限制解除，不能直接结案"})
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





