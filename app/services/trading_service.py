from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.auth import User
from app.models.profile import CustomerHolding, Product
from app.models.trading import Account, Order, OrderStatusHistory, Trade

class TradingError(ValueError):
    pass

class TradingService:
    async def get_or_create_account(self, session: AsyncSession, user_id: str) -> Account:
        account = (await session.execute(select(Account).where(Account.user_id == user_id))).scalar_one_or_none()
        if account is None:
            account = Account(id=str(uuid4()), user_id=user_id, account_no=f"AC{user_id[:10].upper()}", available_balance=Decimal("200000.00"))
            session.add(account)
            await session.flush()
        return account

    async def _order_response(self, session, order):
        product = (await session.execute(select(Product).where(Product.id == order.product_id))).scalar_one_or_none()
        return order, product.name if product else None

    async def create_order(self, session: AsyncSession, user: User, product_id: str, amount: Decimal, idempotency_key: str | None = None):
        if idempotency_key:
            existing = (await session.execute(select(Order).where(Order.user_id == user.id, Order.idempotency_key == idempotency_key))).scalar_one_or_none()
            if existing is not None:
                product = (await session.execute(select(Product).where(Product.id == existing.product_id))).scalar_one_or_none()
                return existing, product.name if product else None
        product = (await session.execute(select(Product).where(Product.id == product_id, Product.status == "active"))).scalar_one_or_none()
        if product is None: raise TradingError("product unavailable")
        if amount < Decimal(str(product.minimum_amount)): raise TradingError(f"minimum amount is {product.minimum_amount}")
        account = await self.get_or_create_account(session, user.id)
        if account.status != "active": raise TradingError("account inactive")
        if account.available_balance < amount: raise TradingError("insufficient available balance")
        order = Order(id=str(uuid4()), order_no=f"O{uuid4().hex[:16].upper()}", user_id=user.id, account_id=account.id, product_id=product.id, amount=amount, quantity=amount, status="pending_confirmation", idempotency_key=idempotency_key)
        session.add(order); await session.flush()
        session.add(OrderStatusHistory(id=str(uuid4()), order_id=order.id, from_status=None, to_status=order.status, operator_user_id=user.id, note="customer created order"))
        return order, product.name

    async def transition(self, session, order, to_status, operator_id, note=""):
        old = order.status; order.status = to_status
        if note: order.review_note = note
        session.add(OrderStatusHistory(id=str(uuid4()), order_id=order.id, from_status=old, to_status=to_status, operator_user_id=operator_id, note=note))
        await session.flush()

    async def confirm_order(self, session, user: User, order_id: str):
        order = (await session.execute(select(Order).where(Order.id == order_id, Order.user_id == user.id).with_for_update())).scalar_one_or_none()
        if order is None: raise TradingError("order not found")
        if order.status != "pending_confirmation": raise TradingError("order cannot be confirmed")
        account = (await session.execute(select(Account).where(Account.id == order.account_id).with_for_update())).scalar_one()
        if account.available_balance < order.amount: raise TradingError("insufficient available balance")
        account.available_balance -= order.amount; account.frozen_balance += order.amount
        await self.transition(session, order, "pending_review", user.id, "customer confirmed order")
        return order

    async def cancel_order(self, session, user: User, order_id: str):
        order = (await session.execute(select(Order).where(Order.id == order_id, Order.user_id == user.id).with_for_update())).scalar_one_or_none()
        if order is None: raise TradingError("order not found")
        if order.status not in {"pending_confirmation", "pending_review"}: raise TradingError("order cannot be cancelled")
        if order.status == "pending_review":
            account = (await session.execute(select(Account).where(Account.id == order.account_id).with_for_update())).scalar_one()
            account.available_balance += order.amount; account.frozen_balance -= order.amount
        await self.transition(session, order, "cancelled", user.id, "customer cancelled order")
        return order

    async def review_order(self, session, operator: User, order_id: str, approve: bool, note: str = ""):
        order = (await session.execute(select(Order).where(Order.id == order_id).with_for_update())).scalar_one_or_none()
        if order is None: raise TradingError("order not found")
        if order.status != "pending_review": raise TradingError("order is not awaiting review")
        if not approve:
            account = (await session.execute(select(Account).where(Account.id == order.account_id).with_for_update())).scalar_one()
            account.available_balance += order.amount; account.frozen_balance -= order.amount
            await self.transition(session, order, "rejected", operator.id, note or "rejected by staff")
            order.failure_reason = note or "rejected by staff"
            return order
        await self.transition(session, order, "executing", operator.id, note or "approved by staff")
        account = (await session.execute(select(Account).where(Account.id == order.account_id).with_for_update())).scalar_one()
        account.frozen_balance -= order.amount
        holding = (await session.execute(select(CustomerHolding).where(CustomerHolding.user_id == order.user_id, CustomerHolding.product_id == order.product_id, CustomerHolding.status == "active").with_for_update())).scalar_one_or_none()
        if holding is None:
            holding = CustomerHolding(id=str(uuid4()), user_id=order.user_id, product_id=order.product_id, quantity=order.quantity, cost_amount=order.amount, market_value=order.amount, profit_loss=0, holding_days=0, status="active"); session.add(holding)
        else:
            holding.quantity += order.quantity; holding.cost_amount += order.amount; holding.market_value += order.amount
        session.add(Trade(id=str(uuid4()), trade_no=f"T{uuid4().hex[:16].upper()}", order_id=order.id, user_id=order.user_id, product_id=order.product_id, amount=order.amount, quantity=order.quantity))
        await self.transition(session, order, "executed", operator.id, note or "mock trade executed")
        return order
