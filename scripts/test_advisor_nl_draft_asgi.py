"""In-process ASGI verification of advisor NL proxy-order draft (uses current code + real DB)."""
import asyncio
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app, lifespan  # noqa: E402
from app.core.settings import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402

BASE = "http://test/api/v1"
ADVISOR = {"username": "advisor_demo", "password": "Demo@2026Advisor"}
CUSTOMER = {"username": "customer_demo", "password": "Demo@2026Customer"}


async def main() -> None:
    database = Database(get_settings())
    created_auth = None
    draft_ids = []
    try:
        async with database.engine.connect() as conn:
            customer_id = str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            advisor_id = str((await conn.execute(text("SELECT id FROM users WHERE username='advisor_demo'"))).scalar_one())
            product_id, product_name = tuple((await conn.execute(
                text("SELECT id, name FROM product WHERE risk_level='C1' AND status='active' ORDER BY minimum_amount LIMIT 1")
            )).first())
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                async def login(acct):
                    resp = await client.post("/auth/login", json=acct)
                    resp.raise_for_status()
                    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

                adv_h = await login(ADVISOR)
                cust_h = await login(CUSTOMER)

                # 1) no auth -> precheck_failed
                no_auth = await client.post(
                    "/profile/staff/operation-drafts/natural-language", headers=adv_h,
                    json={"customer_id": customer_id, "query": f"代客户申购“{product_name}”1000元"},
                )
                print("no_auth status:", no_auth.status_code, no_auth.json().get("data", {}).get("status"), no_auth.json().get("data", {}).get("clarification", ""))

                # 2) grant advisor buy scope for this product
                grant = await client.post(
                    "/profile/me/advisor-authorizations", headers=cust_h,
                    json={"advisor_id": advisor_id, "allowed_actions": "buy", "product_scope": product_id,
                          "max_single_amount": 50000, "max_daily_amount": 100000,
                          "risk_level_ceiling": "C2", "requires_customer_confirmation": True},
                )
                grant.raise_for_status()
                created_auth = grant.json()["data"]["id"]
                print("grant created:", created_auth)

                # 3) missing amount -> needs_clarification
                miss = await client.post(
                    "/profile/staff/operation-drafts/natural-language", headers=adv_h,
                    json={"customer_id": customer_id, "query": f"代客户申购“{product_name}”"},
                )
                print("missing amount:", miss.status_code, miss.json().get("data", {}).get("status"))

                # 4) OK -> awaiting_customer_confirmation draft
                ok = await client.post(
                    "/profile/staff/operation-drafts/natural-language", headers=adv_h,
                    json={"customer_id": customer_id, "query": f"代客户申购“{product_name}”1000元"},
                )
                okj = ok.json().get("data", {}) if ok.status_code == 200 else {}
                draft_id = okj.get("draft", {}).get("id") if isinstance(okj.get("draft"), dict) else None
                if draft_id:
                    draft_ids.append(draft_id)
                print("OK:", ok.status_code, okj.get("status"), "draft:", draft_id)

                # 5) customer confirm
                if draft_id:
                    conf = await client.post(f"/profile/me/operation-drafts/{draft_id}/confirm", headers=cust_h)
                    print("confirm:", conf.status_code, conf.json().get("data", {}).get("status"))

                passed = (
                    no_auth.status_code == 200 and no_auth.json().get("data", {}).get("status") == "precheck_failed"
                    and miss.status_code == 200 and miss.json().get("data", {}).get("status") == "needs_clarification"
                    and ok.status_code == 200 and okj.get("status") == "awaiting_customer_confirmation" and bool(draft_id)
                )
                print("PASS:", passed)
                if not passed:
                    raise SystemExit(1)
    finally:
        async with database.engine.begin() as conn:
            for did in draft_ids:
                await conn.execute(text("DELETE FROM advisor_operation_draft WHERE id=:id"), {"id": did})
            if created_auth:
                await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='advisor_authorization' AND resource_id=:id"), {"id": created_auth})
                await conn.execute(text("DELETE FROM customer_advisor_authorization WHERE id=:id"), {"id": created_auth})
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
