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

BASE = "http://test/api/v1"


async def login(client: httpx.AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def customer_id(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.get("/auth/me", headers=headers)
    response.raise_for_status()
    return response.json()["data"]["id"]


async def execute(client: httpx.AsyncClient, headers: dict[str, str], query: str) -> dict:
    response = await client.post(
        "/customer-service/sessions/execute-plan",
        headers={**headers, "X-Conversation-Id": f"entity-exact-{uuid.uuid4()}"},
        json={"note": query},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def main() -> None:
    db = Database(get_settings())
    ids: dict[str, str] = {}
    tag = uuid.uuid4().hex[:8].upper()
    checks: dict[str, bool] = {}
    try:
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30) as client:
                customer = await login(client, "customer_demo", "Demo@2026Customer")
                other_customer = await login(client, "hNW_demo", "Demo@2026HighNetWorth")
                user_id = await customer_id(client, customer)
                other_user_id = await customer_id(client, other_customer)
                ids = {
                    "product": str(uuid.uuid4()),
                    "account": str(uuid.uuid4()),
                    "order_target": str(uuid.uuid4()),
                    "order_other": str(uuid.uuid4()),
                    "order_foreign": str(uuid.uuid4()),
                    "work_target": str(uuid.uuid4()),
                    "work_other": str(uuid.uuid4()),
                    "product_same_a": str(uuid.uuid4()),
                    "product_same_b": str(uuid.uuid4()),
                }
                product_a_name = f"同名产品{tag}"
                product_b_name = f"同名产品{tag}"
                order_target_no = f"ORD-EXACT-{tag}-A"
                order_other_no = f"ORD-EXACT-{tag}-B"
                order_foreign_no = f"ORD-EXACT-{tag}-X"
                ticket_target_no = f"WO-EXACT-{tag}-A"
                ticket_other_no = f"WO-EXACT-{tag}-B"
                async with db.engine.begin() as conn:
                    await conn.execute(text("""
                        INSERT INTO product (id, name, product_type, risk_level, term_days, minimum_amount, daily_yield_rate, liquidity, description, target_customer_type, status, source_type)
                        VALUES (:id, :name, 'fund', 'C2', 30, 1000, 0, 'medium', 'entity exact test', 'individual', 'active', 'test')
                    """), {"id": ids["product"], "name": f"精确实体产品{tag}"})
                    existing_account = (await conn.execute(text("SELECT id FROM account WHERE user_id=:user_id LIMIT 1"), {"user_id": user_id})).scalar_one_or_none()
                    if existing_account:
                        ids["account"] = existing_account
                    else:
                        await conn.execute(text("""
                            INSERT INTO account (id, user_id, account_no, currency, available_balance, frozen_balance, status, risk_restriction_status, risk_restriction_reason)
                            VALUES (:id, :user_id, :account_no, 'CNY', 10000, 0, 'active', 'none', '')
                        """), {"id": ids["account"], "user_id": user_id, "account_no": f"ENTITY-{tag}"})
                        ids["account_created"] = ids["account"]
                    for product_id, product_name in [(ids["product_same_a"], product_a_name), (ids["product_same_b"], product_b_name)]:
                        await conn.execute(text("""
                            INSERT INTO product (id, name, product_type, risk_level, term_days, minimum_amount, daily_yield_rate, liquidity, description, target_customer_type, status, source_type)
                            VALUES (:id, :name, 'fund', 'C2', 30, 1000, 0, 'medium', 'same name test', 'individual', 'active', 'test')
                        """), {"id": product_id, "name": product_name})
                    for key, order_no, owner, status in [
                        ("order_target", order_target_no, user_id, "pending_review"),
                        ("order_other", order_other_no, user_id, "executed"),
                        ("order_foreign", order_foreign_no, other_user_id, "executed"),
                    ]:
                        await conn.execute(text("""
                            INSERT INTO orders (id, order_no, user_id, account_id, product_id, amount, quantity, status, side, review_note, failure_reason, order_source, confirmation_mode)
                            VALUES (:id, :order_no, :user_id, :account_id, :product_id, 1000, 1, :status, 'buy', '', '', 'customer_direct', 'customer_order')
                        """), {"id": ids[key], "order_no": order_no, "user_id": owner, "account_id": ids["account"], "product_id": ids["product"], "status": status})
                    for key, ticket_no, title in [("work_target", ticket_target_no, "目标工单"), ("work_other", ticket_other_no, "其他工单")]:
                        await conn.execute(text("""
                            INSERT INTO work_order (id, ticket_no, customer_id, created_by, category, priority, status, title, description, context_json, resolution_note)
                            VALUES (:id, :ticket_no, :customer_id, :created_by, 'customer_service', 'normal', 'in_progress', :title, 'entity exact test', '{}', '')
                        """), {"id": ids[key], "ticket_no": ticket_no, "customer_id": user_id, "created_by": user_id, "title": title})

                order_data = await execute(client, customer, f"查询订单号 {order_target_no} 的订单状态")
                order_result = next(item for item in order_data["results"] if item["intent"] == "order_status")
                order_items = order_result["data"].get("items", [])
                checks["target_order_only"] = [item["order_no"] for item in order_items] == [order_target_no]

                work_data = await execute(client, customer, f"查看工单号 {ticket_target_no} 的进度")
                work_result = next(item for item in work_data["results"] if item["intent"] == "workorder_status")
                work_items = work_result["data"].get("items", [])
                checks["target_ticket_only"] = [item["ticket_no"] for item in work_items] == [ticket_target_no]

                product_data = await execute(client, customer, f"查询产品名称：{product_a_name}现在是否在售")
                product_result = next(item for item in product_data["results"] if item["intent"] == "product_status")
                checks["duplicate_product_requires_clarification"] = product_result["status"] == "needs_clarification" and product_result.get("clarification")

                foreign_data = await execute(client, customer, f"查询订单号 {order_foreign_no} 的订单状态")
                foreign_result = next(item for item in foreign_data["results"] if item["intent"] == "order_status")
                checks["foreign_order_not_disclosed"] = all(item.get("order_no") != order_foreign_no for item in foreign_result["data"].get("items", [])) and order_foreign_no not in foreign_result.get("answer", "")

        failed = [name for name, passed in checks.items() if not passed]
        print(json.dumps({"checks": checks, "pass": not failed, "failed": failed}, ensure_ascii=False, indent=2))
        if failed:
            raise AssertionError(failed)
    finally:
        async with db.engine.begin() as conn:
            if ids:
                await conn.execute(text("DELETE FROM orders WHERE id = ANY(:ids)"), {"ids": [ids.get("order_target", ""), ids.get("order_other", ""), ids.get("order_foreign", "")]})
                await conn.execute(text("DELETE FROM work_order WHERE id = ANY(:ids)"), {"ids": [ids.get("work_target", ""), ids.get("work_other", "")]})
                await conn.execute(text("DELETE FROM product WHERE id = ANY(:ids)"), {"ids": [ids.get("product", ""), ids.get("product_same_a", ""), ids.get("product_same_b", "")]})
                if ids.get("account_created"):
                    await conn.execute(text("DELETE FROM account WHERE id=:id"), {"id": ids["account_created"]})
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())


