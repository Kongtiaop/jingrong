from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import aiomysql
from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.trading import Order, Trade, OrderStatusHistory

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")


def norm(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def comparable_order(row):
    return {k: norm(getattr(row, k)) for k in ("id", "order_no", "user_id", "account_id", "product_id", "amount", "quantity", "status", "side", "idempotency_key")}

def comparable_trade(row):
    return {k: norm(getattr(row, k)) for k in ("id", "trade_no", "order_id", "user_id", "product_id", "amount", "quantity")}

def comparable_history(row):
    return {k: norm(getattr(row, k)) for k in ("id", "order_id", "from_status", "to_status", "operator_user_id", "note")}

async def main():
    db = Database(get_settings())
    mysql = await aiomysql.connect(**MYSQL, autocommit=True)
    try:
        async with db.session_factory() as session:
            pg_orders = list((await session.execute(select(Order))).scalars().all())
            pg_trades = list((await session.execute(select(Trade))).scalars().all())
            pg_history = list((await session.execute(select(OrderStatusHistory))).scalars().all())
        async with mysql.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT * FROM orders")
            my_orders = await cur.fetchall()
            await cur.execute("SELECT * FROM trades")
            my_trades = await cur.fetchall()
            await cur.execute("SELECT * FROM order_status_history")
            my_history = await cur.fetchall()
        my_orders_by_id = {str(r["id"]): r for r in my_orders}
        my_trades_by_id = {str(r["id"]): r for r in my_trades}
        my_history_by_id = {str(r["id"]): r for r in my_history}
        order_mismatches=[]; trade_mismatches=[]; history_mismatches=[]
        for row in pg_orders:
            m=my_orders_by_id.get(str(row.id))
            if not m: order_mismatches.append({"id":str(row.id),"reason":"missing_mysql"})
            else:
                expected=comparable_order(row)
                actual={k:norm(m.get(k)) for k in expected}
                if expected != actual: order_mismatches.append({"id":str(row.id),"expected":expected,"actual":actual})
        for row in pg_trades:
            m=my_trades_by_id.get(str(row.id))
            if not m: trade_mismatches.append({"id":str(row.id),"reason":"missing_mysql"})
            else:
                expected=comparable_trade(row); actual={k:norm(m.get(k)) for k in expected}
                if expected != actual: trade_mismatches.append({"id":str(row.id),"expected":expected,"actual":actual})
        for row in pg_history:
            m=my_history_by_id.get(str(row.id))
            if not m: history_mismatches.append({"id":str(row.id),"reason":"missing_mysql"})
            else:
                expected=comparable_history(row); actual={k:norm(m.get(k)) for k in expected}
                if expected != actual: history_mismatches.append({"id":str(row.id),"expected":expected,"actual":actual})
        result={"postgres_counts":{"orders":len(pg_orders),"trades":len(pg_trades),"history":len(pg_history)},"mysql_counts":{"orders":len(my_orders),"trades":len(my_trades),"history":len(my_history)},"missing_or_mismatched":{"orders":order_mismatches,"trades":trade_mismatches,"history":history_mismatches}}
        result["extra_mysql"] = {
            "orders": sorted(set(my_orders_by_id) - {str(row.id) for row in pg_orders}),
            "trades": sorted(set(my_trades_by_id) - {str(row.id) for row in pg_trades}),
            "history": sorted(set(my_history_by_id) - {str(row.id) for row in pg_history}),
        }
        result["matching_existing_rows"] = not any(result["missing_or_mismatched"].values())
        result["pass"] = result["matching_existing_rows"]
        result["note"] = "Extra MySQL rows are reported separately because the migration database may contain prior probe/test rows; existing PostgreSQL rows must match exactly."
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        if not result["pass"]: raise SystemExit(1)
    finally:
        mysql.close(); await db.dispose()

if __name__ == "__main__": asyncio.run(main())
