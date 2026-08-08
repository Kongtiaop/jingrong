from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import httpx
from sqlalchemy import text
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.settings import get_settings
from app.db.session import Database
from app.main import app, lifespan
BASE = "http://test/api/v1"
async def login(client, username, password):
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
async def main():
    db = Database(get_settings())
    session_id = None
    try:
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                customer = await login(client, "customer_demo", "Demo@2026Customer")
                agent = await login(client, "customer_service_demo", "Demo@2026CustomerService")
                created = await client.post("/customer-service/sessions", headers=customer, json={"note": "realtime_worker_sweep"})
                assert created.status_code == 201, created.text
                session_id = created.json()["data"]["id"]
                claimed = await client.post(f"/customer-service/sessions/{session_id}/claim", headers=agent)
                assert claimed.status_code == 200, claimed.text
                heartbeat = await client.post(f"/customer-service/sessions/{session_id}/heartbeat", headers=agent)
                assert heartbeat.status_code == 200, heartbeat.text
                old = datetime.now(timezone.utc) - timedelta(seconds=120)
                async with db.engine.begin() as conn:
                    await conn.execute(text("UPDATE customer_service_session SET agent_last_heartbeat_at=:old WHERE id=:id"), {"old": old, "id": session_id})
                await asyncio.sleep(35)
                async with db.engine.connect() as conn:
                    row = (await conn.execute(text("SELECT status, assigned_agent_id, agent_last_heartbeat_at FROM customer_service_session WHERE id=:id"), {"id": session_id})).mappings().one()
                assert row["status"] == "paused", dict(row)
                assert row["assigned_agent_id"] is None, dict(row)
                assert row["agent_last_heartbeat_at"] is None, dict(row)
                print({"waited_seconds": 35, "worker_auto_paused": True, "assignment_cleared": True, "pass": True})
    finally:
        if session_id:
            async with db.engine.begin() as conn:
                await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='customer_service_session' AND resource_id=:id"), {"id": session_id})
                await conn.execute(text("DELETE FROM customer_service_session WHERE id=:id"), {"id": session_id})
        await db.dispose()
if __name__ == "__main__":
    asyncio.run(main())
