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

async def login(client, username, password):
    r=await client.post("/auth/login", json={"username":username,"password":password}); r.raise_for_status()
    return {"Authorization":f"Bearer {r.json()['data']['access_token']}"}

async def main():
    db=Database(get_settings()); ids=[]; checks={}
    try:
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                customer=await login(client,"customer_demo","Demo@2026Customer")
                agent=await login(client,"customer_service_demo","Demo@2026CustomerService")
                supervisor=await login(client,"super_admin_demo","Demo@2026SuperAdmin")
                created=await client.post("/customer-service/sessions",headers=customer,json={"note":"customer_requested"})
                checks["customer_create"] = created.status_code == 201
                sid=created.json()["data"]["id"]; ids.append(sid)
                listed=await client.get("/customer-service/sessions?status=waiting_human",headers=agent)
                checks["agent_queue"] = listed.status_code == 200 and any(x["id"]==sid for x in listed.json()["data"])
                claimed=await client.post(f"/customer-service/sessions/{sid}/claim",headers=agent)
                checks["claim"] = claimed.status_code == 200 and claimed.json()["data"]["status"] == "human_active"
                conflict=await client.post(f"/customer-service/sessions/{sid}/claim",headers=agent)
                checks["same_agent_cannot_claim_active"] = conflict.status_code == 409
                paused=await client.post(f"/customer-service/sessions/{sid}/pause",headers=agent,json={"note":"暂时查询资料"})
                checks["pause_releases_active_state"] = paused.status_code == 200 and paused.json()["data"]["status"] == "paused"
                released=await client.post(f"/customer-service/sessions/{sid}/release",headers=agent,json={"note":"转回队列"})
                checks["release"] = released.status_code == 409
                claimed2=await client.post(f"/customer-service/sessions/{sid}/claim",headers=agent)
                checks["reclaim_after_pause"] = claimed2.status_code == 200 and claimed2.json()["data"]["status"] == "human_active"
                resolved=await client.post(f"/customer-service/sessions/{sid}/resolve",headers=agent,json={"note":"已完成人工说明"})
                checks["resolve_requires_note_and_works"] = resolved.status_code == 200 and resolved.json()["data"]["status"] == "resolved"
                closed=await client.post(f"/customer-service/sessions/{sid}/close",headers=agent)
                checks["close"] = closed.status_code == 200 and closed.json()["data"]["status"] == "closed"
                customer_close=await client.post(f"/customer-service/sessions/{sid}/customer-close",headers=customer,json={"note":"customer_requested_close"})
                checks["closed_cannot_reopen_by_customer"] = customer_close.status_code == 409
                unauth=await client.get("/customer-service/sessions")
                checks["authentication"] = unauth.status_code == 401
                # Concurrent claims on a second session: only one request may win.
                created2=await client.post("/customer-service/sessions",headers=customer,json={"note":"concurrent_claim"})
                sid2=created2.json()["data"]["id"]; ids.append(sid2)
                results=await asyncio.gather(*[client.post(f"/customer-service/sessions/{sid2}/claim",headers=agent) for _ in range(2)])
                checks["concurrent_claim_single_winner"] = sorted(r.status_code for r in results) == [200,409]
        failed=[k for k,v in checks.items() if not v]
        print(json.dumps({"checks":checks,"pass":not failed,"failed":failed},ensure_ascii=False,indent=2))
        if failed: raise AssertionError(failed)
    finally:
        async with db.engine.begin() as conn:
            if ids:
                await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='customer_service_session' AND resource_id = ANY(:ids)"), {"ids":ids})
                await conn.execute(text("DELETE FROM customer_service_session WHERE id = ANY(:ids)"), {"ids":ids})
        await db.dispose()

if __name__=='__main__': asyncio.run(main())
