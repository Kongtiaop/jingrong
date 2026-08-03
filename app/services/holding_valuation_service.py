from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerHolding, Product

MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.00000001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def holding_days(holding: CustomerHolding, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    created_at = holding.created_at or now
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0, (now.date() - created_at.date()).days)


def value_for(cost_amount: Decimal, daily_yield_rate: Decimal, days: int) -> tuple[Decimal, Decimal, Decimal]:
    rate = Decimal(str(daily_yield_rate or 0)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    cumulative = (Decimal("1") + rate) ** max(0, days) - Decimal("1")
    market_value = _money(Decimal(str(cost_amount or 0)) * (Decimal("1") + cumulative))
    return market_value, _money(market_value - Decimal(str(cost_amount or 0))), cumulative


class HoldingValuationService:
    """Deterministic demo valuation: fixed non-negative daily product yields."""

    async def revalue_user(self, session: AsyncSession, user_id: str, now: datetime | None = None) -> list[CustomerHolding]:
        now = now or datetime.now(timezone.utc)
        holdings = list((await session.execute(select(CustomerHolding).where(CustomerHolding.user_id == user_id, CustomerHolding.status == "active").with_for_update())).scalars().all())
        if not holdings:
            return []
        product_ids = {holding.product_id for holding in holdings}
        products = {product.id: product for product in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()}
        for holding in holdings:
            product = products.get(holding.product_id)
            days = holding_days(holding, now)
            rate = Decimal(str(product.daily_yield_rate if product else 0))
            market_value, profit_loss, _ = value_for(Decimal(str(holding.cost_amount)), rate, days)
            holding.holding_days = days
            holding.market_value = market_value
            holding.profit_loss = profit_loss
        await session.flush()
        return holdings

    async def details_for(self, session: AsyncSession, holdings: list[CustomerHolding], now: datetime | None = None) -> dict[str, dict]:
        now = now or datetime.now(timezone.utc)
        product_ids = {holding.product_id for holding in holdings}
        products = {product.id: product for product in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()} if product_ids else {}
        output = {}
        for holding in holdings:
            product = products.get(holding.product_id)
            days = holding_days(holding, now)
            rate = Decimal(str(product.daily_yield_rate if product else 0))
            _, _, cumulative = value_for(Decimal(str(holding.cost_amount)), rate, days)
            output[holding.id] = {
                "product_name": product.name if product else None,
                "daily_yield_rate": float(rate),
                "cumulative_return_rate": float(cumulative),
            }
        return output
