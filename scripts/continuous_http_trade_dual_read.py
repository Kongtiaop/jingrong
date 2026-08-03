from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from decimal import Decimal

import aiomysql
import httpx
from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.trading import Order, Trade
from app.models.profile import CustomerAssetSnapshot, CustomerHolding

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")


def api_data(response):
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(payload)
    return payload["data"]


def normalize(value):
    if value is None:
        return None
    text = str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    return text


def normalize_dict(value):
    if value is None:
        return None
    return {key: normalize(item) for key, item in value.items()}


def comparable_order(row):
    return None if row is None else {key: normalize(row.get(key)) for key in ("id", "order_no", "product_id", "amount", "quantity", "status", "side")}


def comparable_asset(row):
    return None if row is None else {key: normalize(row.get(key)) for key in ("user_id", "total_asset", "cash_balance", "investable_asset", "liability", "net_asset", "source_type")}


async def pg_snapshot(order_id: str | None = None):
    db = Database(get_settings())
    try:
        async with db.session_factory() as session:
            user = (await session.execute(select(User).where(User.username == "customer_demo"))).scalar_one()
            order_query = select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc())
            if order_id:
                order_query = select(Order).where(Order.id == order_id, Order.user_id == user.id)
            order = (await session.execute(order_query)).scalars().first()
            trades = list((await session.execute(select(Trade).where(Trade.user_id == user.id).order_by(Trade.executed_at.desc()))).scalars().all())
            holdings = list((await session.execute(select(CustomerHolding).where(CustomerHolding.user_id == user.id, CustomerHolding.product_id == order.product_id, CustomerHolding.status == "active"))).scalars().all()) if order else []
            asset = (await session.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id == user.id).order_by(CustomerAssetSnapshot.snapshot_time.desc(), CustomerAssetSnapshot.created_at.desc()).limit(1))).scalars().first()
            from app.models.trading import OrderStatusHistory
            history = list((await session.execute(select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id).order_by(OrderStatusHistory.created_at))).scalars().all()) if order else []
            return {
                "user_id": str(user.id),
                "order": None if order is None else {k: normalize(getattr(order, k)) for k in ("id", "order_no", "product_id", "amount", "quantity", "status", "side")},
                "trade_ids": [str(t.id) for t in trades],
                "holding": None if not holdings else {k: normalize(getattr(holdings[0], k)) for k in ("id", "user_id", "product_id", "quantity", "cost_amount", "market_value", "profit_loss", "holding_days", "status")},
                "asset": None if asset is None else {k: normalize(getattr(asset, k)) for k in ("id", "user_id", "total_asset", "cash_balance", "investable_asset", "liability", "net_asset", "source_type")},
                "history": sorted([{k: normalize(getattr(h, k)) for k in ("id", "order_id", "from_status", "to_status", "operator_user_id", "note")} for h in history], key=lambda item: item["id"]),
            }
    finally:
        await db.dispose()


async def mysql_snapshot(order_id: str):
    conn = await aiomysql.connect(**MYSQL, autocommit=True)
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT id,order_no,user_id,product_id,amount,quantity,status,side FROM orders WHERE id=%s", (order_id,))
            order = await cur.fetchone()
            if order is None:
                return {"order": None, "trade_ids": [], "holding": None, "asset": None, "history": []}
            user_id = str(order["user_id"])
            await cur.execute("SELECT id FROM trades WHERE order_id=%s", (order_id,))
            trades = await cur.fetchall()
            await cur.execute("SELECT id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status FROM customer_holding WHERE user_id=%s AND product_id=%s AND status='active' LIMIT 1", (user_id, order["product_id"]))
            holding = await cur.fetchone()
            await cur.execute("SELECT id,user_id,total_asset,cash_balance,investable_asset,liability,net_asset,source_type FROM customer_asset_snapshot WHERE user_id=%s ORDER BY snapshot_time DESC,created_at DESC LIMIT 1", (user_id,))
            asset = await cur.fetchone()
            await cur.execute("SELECT id,order_id,from_status,to_status,operator_user_id,note FROM order_status_history WHERE order_id=%s ORDER BY created_at", (order_id,))
            history = await cur.fetchall()
            return {
                "order": {k: normalize(order.get(k)) for k in ("id", "order_no", "user_id", "product_id", "amount", "quantity", "status", "side")},
                "trade_ids": [str(t["id"]) for t in trades],
                "holding": None if holding is None else {k: normalize(holding.get(k)) for k in ("id", "user_id", "product_id", "quantity", "cost_amount", "market_value", "profit_loss", "holding_days", "status")},
                "asset": None if asset is None else {k: normalize(asset.get(k)) for k in ("id", "user_id", "total_asset", "cash_balance", "investable_asset", "liability", "net_asset", "source_type")},
                "history": [{k: normalize(h.get(k)) for k in ("id", "order_id", "from_status", "to_status", "operator_user_id", "note")} for h in history],
            }
    finally:
        conn.close()


async def cleanup(order_id: str):
    db = Database(get_settings())
    try:
        async with db.session_factory() as session:
            order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
            if order is not None:
                from sqlalchemy import delete
                from app.models.trading import OrderStatusHistory
                await session.execute(delete(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id))
                await session.execute(delete(Trade).where(Trade.order_id == order_id))
                await session.delete(order)
                await session.commit()
    finally:
        await db.dispose()
    conn = await aiomysql.connect(**MYSQL, autocommit=False)
    try:
        async with conn.cursor() as cur:
            for table, column in (("order_status_history", "order_id"), ("trades", "order_id"), ("orders", "id")):
                await cur.execute(f"DELETE FROM `{table}` WHERE `{column}`=%s", (order_id,))
            await conn.commit()
    finally:
        conn.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010/api/v1")
    args = parser.parse_args()
    order_id = None
    result = {}
    try:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=15) as client:
            customer = api_data(await client.post("/auth/login", json={"username": "customer_demo", "password": "Demo@2026Customer"}))
            operator = api_data(await client.post("/auth/login", json={"username": "operations_demo", "password": "Demo@2026Operations"}))
            ch = {"Authorization": f"Bearer {customer['access_token']}"}
            oh = {"Authorization": f"Bearer {operator['access_token']}"}
            products = api_data(await client.get("/profile/products", headers=ch))
            product = next(item for item in products if str(item.get("risk_level", "C1")).replace("R", "C") == "C1" and item.get("target_customer_type", "individual") in {"all", "individual"})
            amount = str(max(Decimal(str(product.get("minimum_amount", "1000"))), Decimal("1000")))
            created = api_data(await client.post("/trading/orders", headers=ch, json={"product_id": product["id"], "amount": amount, "idempotency_key": "continuous-" + uuid.uuid4().hex}))
            order_id = created["id"]
            await client.post(f"/trading/orders/{order_id}/confirm", headers=ch)
            executed = api_data(await client.post(f"/trading/orders/{order_id}/review", headers=oh, json={"note": "continuous dual-read"}))
            pg_after_api = await pg_snapshot(order_id)
            detail = api_data(await client.get(f"/trading/orders/{order_id}", headers=ch))
            api_trade_rows = api_data(await client.get("/trading/trades/me", headers=ch))
            result["after_api"] = {"executed_status": executed.get("status"), "detail_order_id": detail.get("id"), "trade_count": len(api_trade_rows)}
            result["pg_before_migration"] = pg_after_api
        # Import here so the script uses exactly the same .env settings as migration.
        from scripts.migrate_core_to_mysql import main as migrate_main
        await migrate_main()
        result["mysql_after_migration"] = await mysql_snapshot(order_id)
        pg = result["pg_before_migration"]
        my = result["mysql_after_migration"]
        result["comparison"] = {
            "order_equal": comparable_order(pg["order"]) == comparable_order(my["order"]),
            "trade_ids_equal": set(pg["trade_ids"]) == set(my["trade_ids"]),
            "holding_equal": normalize_dict(pg["holding"]) == normalize_dict(my["holding"]),
            "asset_equal": comparable_asset(pg["asset"]) == comparable_asset(my["asset"]),
            "history_equal": [normalize_dict(item) for item in pg["history"]] == sorted([normalize_dict(item) for item in my["history"]], key=lambda item: item["id"]),
        }
        result["pass"] = result["after_api"]["executed_status"] == "executed" and result["after_api"]["detail_order_id"] == order_id and all(result["comparison"].values())
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        if order_id:
            await cleanup(order_id)


if __name__ == "__main__": asyncio.run(main())
