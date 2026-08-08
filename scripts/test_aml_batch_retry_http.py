from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database


async def login(client, username, password):
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def main():
    db = Database(get_settings())
    source_id = None
    try:
        async with db.engine.begin() as conn:
            username = (await conn.execute(text("SELECT username FROM users WHERE username='customer_demo'"))).scalar_one()
            source_id = str(uuid4())
            await conn.execute(text("""INSERT INTO aml_batch_run
                (id,batch_key,scan_window_start,scan_window_end,scan_days,status,error_summary,started_at,finished_at)
                VALUES (:id,:key,now()-interval '7 days',now(),7,'completed_with_errors',:errors,now(),now())"""), {
                "id": source_id, "key": f"retry-fixture:{source_id}",
                "errors": json.dumps([{"username": username, "error": "fixture failure"}]),
            })
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8010/api/v1", timeout=30) as client:
            admin = await login(client, "super_admin_demo", "Demo@2026SuperAdmin")
            customer = await login(client, "customer_demo", "Demo@2026Customer")
            forbidden = await client.post(f"/risk-alerts/batches/{source_id}/retry", headers=customer)
            assert forbidden.status_code == 403
            first = await client.post(f"/risk-alerts/batches/{source_id}/retry", headers=admin)
            first.raise_for_status()
            runs = first.json()["data"]["retry_runs"]
            assert len(runs) == 1 and runs[0]["created"] is True
            second = await client.post(f"/risk-alerts/batches/{source_id}/retry", headers=admin)
            second.raise_for_status()
            assert second.json()["data"]["retry_runs"][0]["created"] is False
            print(json.dumps({"customer_forbidden": forbidden.status_code, "first_created": True, "second_reused": True, "pass": True}))
    finally:
        async with db.engine.begin() as conn:
            if source_id:
                await conn.execute(text("DELETE FROM aml_batch_run WHERE id=:id"), {"id": source_id})
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
