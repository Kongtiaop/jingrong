from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, desc
from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_permission, require_staff_permission, staff_customer_user
from app.models.auth import User
from app.models.profile import Product
from app.models.trading import Account, Order, Trade, OrderStatusHistory
from app.schemas.trading import *
from app.services.trading_service import TradingError, TradingService

router = APIRouter(prefix="/trading", tags=["trading"])
service = TradingService()

def err(exc: TradingError):
    raise HTTPException(status_code=400, detail=str(exc)) from exc

async def order_response(session, order):
    product = (await session.execute(select(Product).where(Product.id == order.product_id))).scalar_one_or_none()
    history = list((await session.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id).order_by(OrderStatusHistory.created_at))).scalars().all())
    return OrderResponse.model_validate({**{c: getattr(order, c) for c in ("id","order_no","user_id","product_id","amount","quantity","status","side","review_note","failure_reason","created_at","updated_at")}, "product_name": product.name if product else None, "history": [OrderStatusHistoryResponse.model_validate(item, from_attributes=True) for item in history]})

@router.get("/account/me", response_model=ApiResponse[AccountResponse])
async def account_me(request: Request, user: Annotated[User, Depends(current_user)]):
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        account = await service.get_or_create_account(session, user.id); await session.commit(); await session.refresh(account)
        return ApiResponse(data=AccountResponse.model_validate(account, from_attributes=True))

@router.post("/orders", response_model=ApiResponse[OrderResponse], status_code=201)
async def create_order(request: Request, payload: OrderCreateRequest, user: Annotated[User, Depends(current_user)]):
    try:
        async with request.app.state.database.session_factory() as session:
            order, _ = await service.create_order(session, user, payload.product_id, payload.amount, payload.idempotency_key); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order))
    except TradingError as exc: err(exc)

@router.get("/orders/me", response_model=ApiResponse[list[OrderResponse]])
async def my_orders(request: Request, user: Annotated[User, Depends(current_user)]):
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(select(Order).where(Order.user_id == user.id).order_by(desc(Order.created_at)))).scalars().all())
        return ApiResponse(data=[await order_response(session, row) for row in rows])

@router.post("/orders/{order_id}/confirm", response_model=ApiResponse[OrderResponse])
async def confirm_order(request: Request, order_id: str, user: Annotated[User, Depends(current_user)]):
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    try:
        async with request.app.state.database.session_factory() as session:
            order = await service.confirm_order(session, user, order_id); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order))
    except TradingError as exc: err(exc)

@router.post("/orders/{order_id}/cancel", response_model=ApiResponse[OrderResponse])
async def cancel_order(request: Request, order_id: str, user: Annotated[User, Depends(current_user)]):
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    try:
        async with request.app.state.database.session_factory() as session:
            order = await service.cancel_order(session, user, order_id); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order))
    except TradingError as exc: err(exc)

@router.get("/orders/pending", response_model=ApiResponse[list[OrderResponse]])
async def pending_orders(request: Request, limit: int = 20, offset: int = 0, user: Annotated[User, Depends(require_permission("order:write"))] = None):
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(select(Order).where(Order.status == "pending_review").order_by(Order.created_at).offset(offset).limit(limit))).scalars().all())
        return ApiResponse(data=[await order_response(session, row) for row in rows])

@router.post("/orders/{order_id}/review", response_model=ApiResponse[OrderResponse])
async def review_order(request: Request, order_id: str, payload: OrderActionRequest, user: Annotated[User, Depends(require_permission("order:write"))]):
    try:
        async with request.app.state.database.session_factory() as session:
            order = await service.review_order(session, user, order_id, True, payload.note); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order))
    except TradingError as exc: err(exc)

@router.post("/orders/{order_id}/reject", response_model=ApiResponse[OrderResponse])
async def reject_order(request: Request, order_id: str, payload: OrderActionRequest, user: Annotated[User, Depends(require_permission("order:write"))]):
    try:
        async with request.app.state.database.session_factory() as session:
            order = await service.review_order(session, user, order_id, False, payload.note); await session.commit(); await session.refresh(order)
            return ApiResponse(data=await order_response(session, order))
    except TradingError as exc: err(exc)

@router.get("/orders/customer/{user_id}", response_model=ApiResponse[list[OrderResponse]])
async def customer_orders(request: Request, user_id: str, user: Annotated[User, Depends(staff_customer_user)]):
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(select(Order).where(Order.user_id == user_id).order_by(desc(Order.created_at)))).scalars().all())
        return ApiResponse(data=[await order_response(session, row) for row in rows])

@router.get("/orders/{order_id}", response_model=ApiResponse[OrderResponse])
async def my_order_detail(request: Request, order_id: str, user: Annotated[User, Depends(current_user)]):
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        order = (await session.execute(select(Order).where(Order.id == order_id, Order.user_id == user.id))).scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return ApiResponse(data=await order_response(session, order))

@router.get("/trades/me", response_model=ApiResponse[list[TradeResponse]])
async def my_trades(request: Request, user: Annotated[User, Depends(current_user)]):
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail="customer account required")
    async with request.app.state.database.session_factory() as session:
        rows = list((await session.execute(select(Trade).where(Trade.user_id == user.id).order_by(desc(Trade.executed_at)))).scalars().all())
        return ApiResponse(data=[TradeResponse.model_validate(row, from_attributes=True) for row in rows])
