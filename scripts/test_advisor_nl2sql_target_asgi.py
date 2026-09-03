"""In-process ASGI verification of advisor NL2SQL data-analysis targeting (capability ①)."""
import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app, lifespan  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402

BASE = "http://test/api/v1"
ADVISOR = {"username": "advisor_demo", "password": "Demo@2026Advisor"}


async def main() -> None:
    database = Database(get_settings())
    try:
        async with database.engine.connect() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            product_id = str((await conn.execute(text("SELECT id FROM product WHERE risk_level='C1' AND status='active' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
            advisor_id = str((await conn.execute(text("SELECT id FROM users WHERE username='advisor_demo'"))).scalar_one())
            # seed a direct active authorization row so SubjectScope allows the advisor to target this customer
            await conn.execute(text(
                "INSERT INTO customer_advisor_authorization (id, customer_id, advisor_id, allowed_actions, product_scope, max_single_amount, max_daily_amount, risk_level_ceiling, effective_from, status, requires_customer_confirmation) "
                "VALUES (:id, :cust, :adv, 'buy', :prod, 100000, 200000, 'C3', NOW(), 'active', true) "
                "ON CONFLICT DO NOTHING"
            ), {"id": "verify-nl2sql-auth", "cust": customer_id, "adv": advisor_id, "prod": product_id})
            await conn.commit()
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                login = await client.post("/auth/login", json=ADVISOR)
                login.raise_for_status()
                headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
                resp = await client.post(
                    "/customer-service/sessions/agent/execute",
                    headers=headers,
                    json={"content": "统计该客户的总资产", "conversation_id": "verify-nl2sql-1", "page_context": "/backoffice", "target_customer_id": customer_id},
                )
                print("analysis status:", resp.status_code)
                data = resp.json().get("data", {})
                print("overall:", data.get("overall_status"), "route:", data.get("route"), "agents:", data.get("agents"))
                print("available_agents:", data.get("available_agents"))
                for item in data.get("results", []):
                    print("result intent:", item.get("intent"), "status:", item.get("status"), "data keys:", list((item.get("data") or {}).keys()))
                    print("  chart_spec:", (item.get("data") or {}).get("chart_spec"))
                passed = resp.status_code == 200 and any((item.get("intent") == "data_analysis") and item.get("data") for item in data.get("results", []))
                print("PASS:", passed)
                if not passed:
                    raise SystemExit(1)
    finally:
        async with database.engine.begin() as conn:
            await conn.execute(text("DELETE FROM customer_advisor_authorization WHERE id='verify-nl2sql-auth'"))
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
