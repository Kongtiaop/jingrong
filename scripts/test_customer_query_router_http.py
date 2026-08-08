from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import get_settings
from app.db.session import Database
from app.main import app, lifespan

BASE = "http://127.0.0.1:8010/api/v1"


async def login(client: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def user_id(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.get("/auth/me", headers=headers)
    response.raise_for_status()
    return response.json()["data"]["id"]


async def route(client: httpx.AsyncClient, headers: dict[str, str], query: str) -> tuple[int, dict]:
    response = await client.post("/customer-rag/route", headers=headers, json={"query": query})
    return response.status_code, response.json()


async def main() -> None:
    checks: dict[str, bool] = {}
    db = Database(get_settings())
    ids: dict[str, str] = {}
    tag = uuid.uuid4().hex[:8]
    try:
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30) as client:
                customer = await login(client, "customer_demo", "Demo@2026Customer")
                other_customer = await login(client, "hNW_demo", "Demo@2026HighNetWorth")
                customer_id = await user_id(client, customer)
                other_customer_id = await user_id(client, other_customer)

                async with db.engine.begin() as conn:
                    ids["product"] = str(uuid.uuid4())
                    ids["work_order"] = str(uuid.uuid4())
                    ids["order"] = str(uuid.uuid4())
                    ids["account"] = str(uuid.uuid4())
                    ids["asset"] = str(uuid.uuid4())
                    ids["holding"] = str(uuid.uuid4())
                    ids["foreign_order"] = str(uuid.uuid4())
                    await conn.execute(text("""
                        INSERT INTO product (id, name, product_type, risk_level, term_days, minimum_amount, daily_yield_rate, liquidity, description, target_customer_type, status, source_type)
                        VALUES (:id, :name, 'fund', 'C2', 30, 1000, 0, 'medium', 'router test', 'individual', 'active', 'test')
                    """), {"id": ids["product"], "name": f"路由验证产品{tag}"})
                    existing_account_id = (await conn.execute(text("SELECT id FROM account WHERE user_id = :user_id"), {"user_id": customer_id})).scalar_one_or_none()
                    if existing_account_id:
                        ids["account"] = existing_account_id
                        await conn.execute(text("""
                            UPDATE account
                            SET risk_restriction_status = 'restricted', risk_restriction_reason = '测试风险限制'
                            WHERE id = :id
                        """), {"id": ids["account"]})
                    else:
                        await conn.execute(text("""
                            INSERT INTO account (id, user_id, account_no, currency, available_balance, frozen_balance, status, risk_restriction_status, risk_restriction_reason)
                            VALUES (:id, :user_id, :account_no, 'CNY', 10000, 0, 'active', 'restricted', '测试风险限制')
                        """), {"id": ids["account"], "user_id": customer_id, "account_no": f"ROUTE-{tag}"})
                        ids["account_created"] = ids["account"]
                    await conn.execute(text("""
                        INSERT INTO work_order (id, ticket_no, customer_id, created_by, category, priority, status, title, description, context_json, resolution_note)
                        VALUES (:id, :ticket_no, :customer_id, :created_by, 'customer_service', 'normal', 'in_progress', :title, 'router test', '{}', '')
                    """), {"id": ids["work_order"], "ticket_no": f"WO-ROUTE-{tag}", "customer_id": customer_id, "created_by": customer_id, "title": "路由验证工单"})
                    await conn.execute(text("""
                        INSERT INTO orders (id, order_no, user_id, account_id, product_id, amount, quantity, status, side, review_note, failure_reason, order_source, confirmation_mode)
                        VALUES (:id, :order_no, :user_id, :account_id, :product_id, 3000, 3, 'pending_review', 'buy', '', '', 'customer_direct', 'customer_order')
                    """), {"id": ids["order"], "order_no": f"ORD-ROUTE-{tag}", "user_id": customer_id, "account_id": ids["account"], "product_id": ids["product"]})
                    await conn.execute(text("""
                        INSERT INTO customer_asset_snapshot (id, user_id, total_asset, cash_balance, investable_asset, liability, net_asset, source_type)
                        VALUES (:id, :user_id, 12345, 2345, 10000, 0, 12345, 'test')
                    """), {"id": ids["asset"], "user_id": customer_id})
                    await conn.execute(text("""
                        INSERT INTO customer_holding (id, user_id, product_id, quantity, cost_amount, market_value, profit_loss, holding_days, status)
                        VALUES (:id, :user_id, :product_id, 3, 3000, 3200, 200, 5, 'active')
                    """), {"id": ids["holding"], "user_id": customer_id, "product_id": ids["product"]})
                    await conn.execute(text("""
                        INSERT INTO orders (id, order_no, user_id, account_id, product_id, amount, quantity, status, side, review_note, failure_reason, order_source, confirmation_mode)
                        VALUES (:id, :order_no, :user_id, :account_id, :product_id, 9999, 9, 'executed', 'buy', '', '', 'customer_direct', 'customer_order')
                    """), {"id": ids["foreign_order"], "order_no": f"ORD-FOREIGN-{tag}", "user_id": other_customer_id, "account_id": ids["account"], "product_id": ids["product"]})

                status, body = await route(client, customer, "我的工单进度怎么样")
                workorders = body.get("data", {})
                checks["workorder_route"] = status == 200 and workorders.get("route") == "workorder_status" and any(item["id"] == ids["work_order"] for item in workorders.get("data", {}).get("items", []))

                status, body = await route(client, customer, "我的订单审核到哪一步了")
                orders = body.get("data", {})
                order_items = orders.get("data", {}).get("items", [])
                checks["order_route_and_own_scope"] = status == 200 and orders.get("route") == "order_status" and any(item["id"] == ids["order"] for item in order_items) and all(item["id"] != ids["foreign_order"] for item in order_items)

                status, body = await route(client, other_customer, "我的订单状态")
                other_orders = body.get("data", {})
                checks["other_customer_cannot_read_first_customer_order"] = status == 200 and all(item["id"] != ids["order"] for item in other_orders.get("data", {}).get("items", []))

                status, body = await route(client, customer, "账户是不是被风险限制了")
                risk = body.get("data", {})
                checks["risk_route"] = status == 200 and risk.get("route") == "risk_restriction" and risk.get("data", {}).get("restriction_status") == "restricted"

                status, body = await route(client, customer, "我现在的资产和持仓")
                assets = body.get("data", {})
                checks["asset_route"] = status == 200 and assets.get("route") == "asset_summary" and assets.get("data", {}).get("latest_asset", {}).get("total_asset") == 12345.0 and any(item["id"] != "" for item in assets.get("data", {}).get("holdings", []))

                status, body = await route(client, customer, f"路由验证产品{tag}现在是否在售")
                product = body.get("data", {})
                checks["product_route"] = status == 200 and product.get("route") == "product_status" and product.get("data", {}).get("items", [{}])[0].get("id") == ids["product"]

                status, body = await route(client, customer, "公司业务是什么")
                fallback = body.get("data", {})
                checks["public_question_falls_back_to_rag"] = status == 200 and fallback.get("source_type") == "rag_explanation" and fallback.get("route") == "public_rag"

                unauthorized = await client.post("/customer-rag/route", json={"query": "我的订单"})
                checks["authentication_required"] = unauthorized.status_code == 401

        failed = [name for name, passed in checks.items() if not passed]
        print(json.dumps({"checks": checks, "pass": not failed, "failed": failed}, ensure_ascii=False, indent=2))
        if failed:
            raise AssertionError(f"failed checks: {failed}")
    finally:
        async with db.engine.begin() as conn:
            if ids:
                await conn.execute(text("DELETE FROM customer_holding WHERE id = :id"), {"id": ids.get("holding", "")})
                await conn.execute(text("DELETE FROM customer_asset_snapshot WHERE id = :id"), {"id": ids.get("asset", "")})
                await conn.execute(text("DELETE FROM orders WHERE id IN (:order_id, :foreign_order_id)"), {"order_id": ids.get("order", ""), "foreign_order_id": ids.get("foreign_order", "")})
                await conn.execute(text("DELETE FROM work_order WHERE id = :id"), {"id": ids.get("work_order", "")})
                if ids.get("account_created"):
                    await conn.execute(text("DELETE FROM account WHERE id = :id"), {"id": ids.get("account", "")})
                elif ids.get("account"):
                    await conn.execute(text("UPDATE account SET risk_restriction_status = 'none', risk_restriction_reason = '' WHERE id = :id"), {"id": ids["account"]})
                await conn.execute(text("DELETE FROM product WHERE id = :id"), {"id": ids.get("product", "")})
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
