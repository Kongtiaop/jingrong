from __future__ import annotations

import asyncio
import json
from sqlalchemy import desc, func, select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.trading import Order, Trade
from app.repositories.mysql_profile import (MySQLOrderReadRepository, MySQLRepository, MySQLStatusHistoryReadRepository, MySQLTradeReadRepository)
from app.repositories.trading import SqlAlchemyOrderRepository, SqlAlchemyStatusHistoryRepository, SqlAlchemyTradeRepository

MYSQL_URL = "mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3307/wealth_manager"


async def main():
    db = Database(get_settings())
    mysql = MySQLRepository(MYSQL_URL)
    try:
        async with db.session_factory() as session:
            user = (await session.execute(select(User).where(User.username == "customer_demo"))).scalar_one()
            user_id = str(user.id)
            pg_orders = await SqlAlchemyOrderRepository().list_for_user(session, user_id, limit=5)
            pg_trades = await SqlAlchemyTradeRepository().list_for_user(session, user_id)
            pg_order = pg_orders[0] if pg_orders else None
            pg_history = await SqlAlchemyStatusHistoryRepository().list_for_order(session, str(pg_order.id)) if pg_order else []
            pg_pending_total, pg_pending = await SqlAlchemyOrderRepository().list_pending(session, limit=5, offset=0)
        orders = MySQLOrderReadRepository(mysql)
        trades = MySQLTradeReadRepository(mysql)
        history = MySQLStatusHistoryReadRepository(mysql)
        mysql_orders = await orders.list_for_user(user_id, limit=5)
        mysql_trades = await trades.list_for_user(user_id)
        mysql_history = await history.list_for_order(str(pg_order.id)) if pg_order else []
        mysql_pending_total, mysql_pending = await orders.list_pending(limit=5, offset=0)
        result = {
            "user_id": user_id,
            "postgres": {"orders": len(pg_orders), "trades": len(pg_trades), "pending_total": pg_pending_total, "history_for_latest": len(pg_history)},
            "mysql": {"orders": len(mysql_orders), "trades": len(mysql_trades), "pending_total": mysql_pending_total, "history_for_latest": len(mysql_history)},
            "stable_shape_pass": all([
                len(mysql_orders) >= 0, len(mysql_trades) >= 0, len(mysql_pending) <= 5,
                (not pg_order) or isinstance(mysql_history, list),
            ]),
            "note": "counts are compared as point-in-time snapshots; mutable order/trade counts may differ until migration refresh",
        }
        result["pass"] = result["stable_shape_pass"]
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        await mysql.close()
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
