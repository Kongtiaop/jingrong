from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select, desc, exists
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.auth import User

CUSTOMER_ROLE = "customer"
from app.models.profile import CustomerHolding, CustomerProfile, CustomerRiskAssessment, Product
from app.models.trading import Account, MoneyFlow, Order, OrderStatusHistory, Trade, RiskAlert
from app.services.asset_summary_service import AssetSummaryService
from app.repositories.trading import SqlAlchemyTradingUnitOfWork
from app.repositories.unit_of_work import create_configured_unit_of_work
from app.services.risk_alert_service import RiskAlertService

class TradingError(ValueError):
    pass

class TradingService:
    def __init__(self, unit_of_work=None, asset_summary_service=None, settings=None):
        if unit_of_work is None and settings is not None:
            unit_of_work = create_configured_unit_of_work(settings)
        self.unit_of_work = unit_of_work or SqlAlchemyTradingUnitOfWork()
        self.asset_summary_service = asset_summary_service or AssetSummaryService(
            account_repository=self.unit_of_work.accounts,
            holding_repository=self.unit_of_work.holdings,
            asset_repository=self.unit_of_work.assets,
        )

    async def get_or_create_account(self, session: AsyncSession, user_id: str) -> Account:
        user = await self.unit_of_work.users.get(session, user_id)
        customer = await self.unit_of_work.users.is_customer(session, user_id)
        if user is None or not customer:
            raise TradingError("only customer accounts can trade")
        account = await self.unit_of_work.accounts.get_by_user(session, user_id)
        if account is None:
            account = await self.unit_of_work.accounts.create_default(session, user_id, f"AC{user_id[:10].upper()}", Decimal("200000.00"))
        return account

    async def _order_response(self, session, order):
        product = await self.unit_of_work.products.get_by_id(session, order.product_id)
        return order, product.name if product else None

    async def create_order(self, session: AsyncSession, user: User, product_id: str, amount: Decimal, idempotency_key: str | None = None, *, order_source: str = "customer_direct", advisor_id: str | None = None, confirmation_mode: str = "customer_order"):
        if order_source not in {"customer_direct", "advisor_assisted"}:
            raise TradingError("invalid order source")
        if order_source == "advisor_assisted" and not advisor_id:
            raise TradingError("advisor id required for advisor order")
        if idempotency_key:
            existing = await self.unit_of_work.orders.get_by_idempotency(session, user.id, idempotency_key)
            if existing is not None:
                product = await self.unit_of_work.products.get_by_id(session, existing.product_id)
                return existing, product.name if product else None
        customer = await self.unit_of_work.users.is_customer(session, user.id)
        if not customer:
            raise TradingError("only customer accounts can trade")
        product = await self.unit_of_work.products.get_active(session, product_id)
        if product is None: raise TradingError("product unavailable")
        if amount < Decimal(str(product.minimum_amount)): raise TradingError(f"minimum amount is {product.minimum_amount}")
        account = await self.get_or_create_account(session, user.id)
        if account.status != "active" or getattr(account, "risk_restriction_status", "none") == "restricted": raise TradingError("account restricted by risk control")
        if account.available_balance < amount: raise TradingError("insufficient available balance")
        order = Order(id=str(uuid4()), order_no=f"O{uuid4().hex[:16].upper()}", user_id=user.id, account_id=account.id, product_id=product.id, amount=amount, quantity=amount, status="pending_confirmation", idempotency_key=idempotency_key, order_source=order_source, advisor_id=advisor_id, confirmation_mode=confirmation_mode)
        await self.unit_of_work.orders.save(session, order)
        await self.unit_of_work.status_history.save(session, OrderStatusHistory(id=str(uuid4()), order_id=order.id, from_status=None, to_status=order.status, operator_user_id=user.id, note="customer created order"))
        return order, product.name

    async def transition(self, session, order, to_status, operator_id, note=""):
        allowed = {
            "pending_confirmation": {"pending_review", "cancelled"},
            "pending_review": {"executing", "rejected", "cancelled"},
            "executing": {"executed"},
            "executed": set(),
            "rejected": set(),
            "cancelled": set(),
        }
        old = order.status
        if to_status == old:
            raise TradingError("order state transition is not allowed")
        if to_status not in allowed.get(old, set()):
            raise TradingError(f"invalid order state transition: {old} -> {to_status}")
        order.status = to_status
        if note: order.review_note = note
        await self.unit_of_work.status_history.save(session, OrderStatusHistory(id=str(uuid4()), order_id=order.id, from_status=old, to_status=to_status, operator_user_id=operator_id, note=note))

    async def confirm_order(self, session, user: User, order_id: str):
        order = await self.unit_of_work.orders.get_for_user(session, order_id, user.id, for_update=True)
        if order is None: raise TradingError("order not found")
        if order.status != "pending_confirmation": raise TradingError("order cannot be confirmed")
        account = await self.unit_of_work.accounts.get_by_id(session, order.account_id, for_update=True)
        if account.available_balance < order.amount: raise TradingError("insufficient available balance")
        from app.services.risk_assessment_service import RiskAssessmentService
        risk = await self.unit_of_work.risks.get_active(session, user.id)
        if risk is None:
            risk = await RiskAssessmentService(repository=self.unit_of_work.risks).ensure_default(session, user.id)
        product = await self.unit_of_work.products.get_by_id(session, order.product_id)
        profile = await self.unit_of_work.profiles.get(session, user.id)
        target_type = getattr(product, "target_customer_type", "individual")
        if target_type != "all" and (profile is None or profile.customer_type != target_type):
            raise TradingError(f"product is only available to {target_type} customers")
        required = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}.get(str(product.risk_level).upper().replace("R", "C"), 1)
        actual = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}.get(risk.risk_level, 1)
        if actual < required: raise TradingError(f"risk level {risk.risk_level} is not suitable for product {product.risk_level}; complete a formal risk assessment to unlock higher-risk products")
        account.available_balance -= order.amount; account.frozen_balance += order.amount
        await self.transition(session, order, "pending_review", user.id, "customer confirmed order")
        return order

    async def cancel_order(self, session, user: User, order_id: str):
        order = await self.unit_of_work.orders.get_for_user(session, order_id, user.id, for_update=True)
        if order is None: raise TradingError("order not found")
        if order.status not in {"pending_confirmation", "pending_review"}: raise TradingError("order cannot be cancelled")
        if order.status == "pending_review":
            account = await self.unit_of_work.accounts.get_by_id(session, order.account_id, for_update=True)
            if account is None: raise TradingError("account not found")
            if order.order_source == "customer_direct":
                if account.frozen_balance < order.amount: raise TradingError("order frozen balance is inconsistent")
                account.available_balance += order.amount; account.frozen_balance -= order.amount
            elif order.order_source == "advisor_assisted":
                if account.available_balance + order.amount < 0: raise TradingError("account balance is inconsistent")
                # Advisor funds are reserved at staff review; before execution there is no refund.
        await self.transition(session, order, "cancelled", user.id, "customer cancelled order")
        return order

    async def _blocking_risk_alert(self, session, order):
        """Return an unresolved confirmed-risk alert that blocks order review."""
        rows = list((await session.execute(select(RiskAlert).where(
            RiskAlert.customer_id == order.user_id,
            RiskAlert.status.in_(["open", "acknowledged", "investigating", "report_pending"]),
            RiskAlert.assessment_result == "confirmed_risk",
        ).order_by(RiskAlert.created_at.desc()))).scalars().all())
        return rows[0] if rows else None


    async def review_order(self, session, operator: User, order_id: str, approve: bool, note: str = ""):
        order = await self.unit_of_work.orders.get_for_update(session, order_id)
        if order is None: raise TradingError("order not found")
        if order.status != "pending_review": raise TradingError("order is not awaiting review")
        risk_block = await self._blocking_risk_alert(session, order)
        if risk_block is not None:
            raise TradingError(f"risk control blocks order: {risk_block.rule_code}")

        account = await self.unit_of_work.accounts.get_by_id(session, order.account_id, for_update=True)
        if account is None:
            raise TradingError("account not found")
        if not approve:
            if order.order_source == "customer_direct":
                if account.frozen_balance < order.amount:
                    raise TradingError("order frozen balance is inconsistent")
                account.available_balance += order.amount
                account.frozen_balance -= order.amount
            elif order.order_source == "advisor_assisted":
                # Advisor orders are not frozen during customer confirmation; no refund is due before debit.
                pass
            await self.unit_of_work.accounts.update_balances(session, account.id, account.available_balance, account.frozen_balance)
            await self.transition(session, order, "rejected", operator.id, note or "rejected by staff")
            order.failure_reason = note or "rejected by staff"
            return order
        if account.frozen_balance >= order.amount:
            account.frozen_balance -= order.amount
        else:
            # Advisor orders are already customer-confirmed but may not have gone through
            # the normal customer confirm endpoint. Reserve the amount at review time.
            if account.available_balance < order.amount:
                raise TradingError("insufficient available balance")
            account.available_balance -= order.amount
        await self.unit_of_work.accounts.update_balances(session, account.id, account.available_balance, account.frozen_balance)
        await self.transition(session, order, "executing", operator.id, note or "approved by staff")
        await self.unit_of_work.accounts.update_balances(session, account.id, account.available_balance, account.frozen_balance)
        holding = await self.unit_of_work.holdings.get_active(session, order.user_id, order.product_id, for_update=True)
        if holding is None:
            holding = CustomerHolding(id=str(uuid4()), user_id=order.user_id, product_id=order.product_id, quantity=order.quantity, cost_amount=order.amount, market_value=order.amount, profit_loss=0, holding_days=0, status="active")
            await self.unit_of_work.holdings.save(session, holding)
        else:
            holding.quantity += order.quantity; holding.cost_amount += order.amount; holding.market_value += order.amount
            await self.unit_of_work.holdings.update_values(session, holding)
        existing_trade = (await session.execute(select(Trade).where(Trade.order_id == order.id))).scalars().first()
        if existing_trade is None:
            await self.unit_of_work.trades.save(session, Trade(id=str(uuid4()), trade_no=f"T{uuid4().hex[:16].upper()}", order_id=order.id, user_id=order.user_id, product_id=order.product_id, amount=order.amount, quantity=order.quantity))
        await self.transition(session, order, "executed", operator.id, note or "mock trade executed")
        await self.asset_summary_service.snapshot(session, order.user_id, source_type="trade_derived")
        existing_flow = (await session.execute(select(MoneyFlow).where(MoneyFlow.order_id == order.id, MoneyFlow.flow_type == "cash", MoneyFlow.direction == "out"))).scalars().first()
        if existing_flow is None:
            flow = MoneyFlow(id=str(uuid4()), customer_id=order.user_id, account_id=order.account_id, order_id=order.id, amount=order.amount, currency="CNY", flow_type="cash", direction="out", channel="wealth_order", occurred_at=order.created_at)
            await self.unit_of_work.money_flows.save(session, flow)
        await RiskAlertService(self.unit_of_work).evaluate_order(session, order)
        return order
















