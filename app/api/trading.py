from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy.exc import IntegrityError
from app.common.response import ApiResponse
from app.common.exceptions import AppException, map_database_error
from app.common.security.auth import current_user, require_permission, require_staff_permission, staff_customer_user
from app.models.auth import User
from app.models.trading import Account, Order, Trade, OrderStatusHistory
from app.schemas.trading import *
from app.services.trading_service import TradingError, TradingService

router = APIRouter(prefix="/trading", tags=["trading"])
def get_trading_service(request: Request) -> TradingService:
    return getattr(request.app.state, "trading_service", service)

service = TradingService()

def err(exc: TradingError):
    message = str(exc)
    code = "TRADING_ERROR"
    status_code = 400
    if message == "order not found":
        code, status_code = "ORDER_NOT_FOUND", 404
    elif message in {"order cannot be confirmed", "order cannot be cancelled", "order is not awaiting review"}:
        code, status_code = "ORDER_STATE_CONFLICT", 409
    elif message.startswith("risk control blocks order:"):
        code, status_code = "RISK_CONTROL_BLOCKED", 409
    elif "insufficient" in message:
        code = "INSUFFICIENT_BALANCE"
    elif message == "product unavailable":
        code, status_code = "PRODUCT_NOT_AVAILABLE", 404
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message}) from exc

async def order_response(session, order, trading_service: TradingService):
    uow = trading_service.unit_of_work
    product = await uow.products.get_by_id(session, order.product_id)
    history = await uow.status_history.list_for_order(session, order.id)
    return OrderResponse.model_validate({**{c: getattr(order, c) for c in ("id","order_no","user_id","product_id","amount","quantity","status","side","review_note","failure_reason","order_source","advisor_id","confirmation_mode","created_at","updated_at")}, "product_name": product.name if product else None, "history": [OrderStatusHistoryResponse.model_validate(item, from_attributes=True) for item in history]})

@router.get("/account/me", response_model=ApiResponse[AccountResponse])
async def account_me(request: Request, user: Annotated[User, Depends(current_user)]):
    trading_service = get_trading_service(request)
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        account = await trading_service.get_or_create_account(session, user.id); await session.commit(); await session.refresh(account)
        return ApiResponse(data=AccountResponse.model_validate(account, from_attributes=True))

@router.post("/orders", response_model=ApiResponse[OrderResponse], status_code=201)
async def create_order(request: Request, payload: OrderCreateRequest, user: Annotated[User, Depends(current_user)]):
    trading_service = get_trading_service(request)
    try:
        async with request.app.state.database.session_factory() as session:
            order, _ = await trading_service.create_order(session, user, payload.product_id, payload.amount, payload.idempotency_key); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order, trading_service))
    except TradingError as exc: err(exc)
    except IntegrityError as exc:
        mapped = map_database_error(exc)
        raise HTTPException(status_code=mapped.status_code, detail={"code": mapped.code, "message": mapped.message}) from exc

@router.get("/orders/me", response_model=ApiResponse[dict])
async def my_orders(request: Request, user: Annotated[User, Depends(current_user)]):
    trading_service = get_trading_service(request)
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        rows = await trading_service.unit_of_work.orders.list_for_user(session, user.id)
        return ApiResponse(data={"items": [await order_response(session, row, trading_service) for row in rows], "total": len(rows)})

@router.post("/orders/{order_id}/confirm", response_model=ApiResponse[OrderResponse])
async def confirm_order(request: Request, order_id: str, user: Annotated[User, Depends(current_user)]):
    trading_service = get_trading_service(request)
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    try:
        async with request.app.state.database.session_factory() as session:
            order = await trading_service.confirm_order(session, user, order_id); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order, trading_service))
    except TradingError as exc: err(exc)
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "ORDER_CONFIRMATION_FAILED", "message": str(exc)}) from exc

@router.post("/orders/{order_id}/cancel", response_model=ApiResponse[OrderResponse])
async def cancel_order(request: Request, order_id: str, user: Annotated[User, Depends(current_user)]):
    trading_service = get_trading_service(request)
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    try:
        async with request.app.state.database.session_factory() as session:
            order = await trading_service.cancel_order(session, user, order_id); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order, trading_service))
    except TradingError as exc: err(exc)

@router.get("/orders/pending", response_model=ApiResponse[OrderListResponse])
async def pending_orders(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("order:write"))] = None):
    trading_service = get_trading_service(request)
    async with request.app.state.database.session_factory() as session:
        total, rows = await trading_service.unit_of_work.orders.list_pending(session, limit=limit, offset=offset)
        return ApiResponse(data=OrderListResponse(items=[await order_response(session, row, trading_service) for row in rows], total=total))

@router.post("/orders/{order_id}/review", response_model=ApiResponse[OrderResponse])
async def review_order(request: Request, order_id: str, payload: OrderActionRequest, user: Annotated[User, Depends(require_permission("order:write"))]):
    trading_service = get_trading_service(request)
    try:
        async with request.app.state.database.session_factory() as session:
            order = await trading_service.review_order(session, user, order_id, True, payload.note); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order, trading_service))
    except TradingError as exc: err(exc)

@router.post("/orders/{order_id}/reject", response_model=ApiResponse[OrderResponse])
async def reject_order(request: Request, order_id: str, payload: OrderActionRequest, user: Annotated[User, Depends(require_permission("order:write"))]):
    trading_service = get_trading_service(request)
    try:
        async with request.app.state.database.session_factory() as session:
            order = await trading_service.review_order(session, user, order_id, False, payload.note); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order, trading_service))
    except TradingError as exc: err(exc)

@router.get("/orders/customer/{user_id}", response_model=ApiResponse[dict])
async def customer_orders(request: Request, user_id: str, user: Annotated[User, Depends(staff_customer_user)]):
    trading_service = get_trading_service(request)
    async with request.app.state.database.session_factory() as session:
        rows = await trading_service.unit_of_work.orders.list_for_user(session, user_id)
        return ApiResponse(data={"items": [await order_response(session, row, trading_service) for row in rows], "total": len(rows)})

@router.get("/orders/{order_id}", response_model=ApiResponse[OrderResponse])
async def my_order_detail(request: Request, order_id: str, user: Annotated[User, Depends(current_user)]):
    trading_service = get_trading_service(request)
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        order = await trading_service.unit_of_work.orders.get_for_user(session, order_id, user.id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return ApiResponse(data=await order_response(session, order, trading_service))

@router.get("/trades/me", response_model=ApiResponse[list[TradeResponse]])
async def my_trades(request: Request, user: Annotated[User, Depends(current_user)]):
    trading_service = get_trading_service(request)
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        rows = await trading_service.unit_of_work.trades.list_for_user(session, user.id)
        return ApiResponse(data=[TradeResponse.model_validate(row, from_attributes=True) for row in rows])





