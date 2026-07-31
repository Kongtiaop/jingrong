from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.session import Database
from app.models.auth import User, Role
from app.models.profile import Product
from app.models.trading import Account, Order, Trade, OrderStatusHistory

async def ensure_trading_seed(database: Database) -> None:
    async with database.session_factory() as session:
        user = (await session.execute(select(User).options(selectinload(User.roles)).where(User.username == "customer_demo"))).scalar_one_or_none()
        if user is None:
            return
        customer_role = (await session.execute(select(Role).where(Role.code == "customer"))).scalar_one_or_none()
        if customer_role is None or customer_role not in user.roles:
            return
        account = (await session.execute(select(Account).where(Account.user_id == user.id))).scalar_one_or_none()
        if account is None:
            account = Account(id=str(uuid4()), user_id=user.id, account_no=f"AC{user.id[:10].upper()}", currency="CNY", available_balance=Decimal("200000.00"), frozen_balance=Decimal("0.00"), status="active")
            session.add(account)
        else:
            account.available_balance = Decimal("200000.00")
            account.frozen_balance = Decimal("0.00")
            account.status = "active"
        # Reset demo transaction state so repeated HTTP tests start from a known balance.
        order_ids = list((await session.execute(select(Order.id).where(Order.user_id == user.id))).scalars().all())
        if order_ids:
            await session.execute(OrderStatusHistory.__table__.delete().where(OrderStatusHistory.order_id.in_(order_ids)))
            await session.execute(Trade.__table__.delete().where(Trade.order_id.in_(order_ids)))
            await session.execute(Order.__table__.delete().where(Order.id.in_(order_ids)))
        await session.commit()
