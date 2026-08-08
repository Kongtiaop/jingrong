from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from sqlalchemy import text
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.settings import get_settings
from app.db.session import Database
from app.main import app, lifespan
from workers.customer_service_timeout_worker import run_timeout_sweep
from app.services.customer_service_timeout import CustomerServiceTimeoutService

BASE='http://test/api/v1'
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 db=Database(get_settings()); sid=None
 try:
  async with lifespan(app):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=BASE) as c:
    customer=await login(c,'customer_demo','Demo@2026Customer'); agent=await login(c,'customer_service_demo','Demo@2026CustomerService')
    created=await c.post('/customer-service/sessions',headers=customer,json={'note':'worker_sweep_test'}); assert created.status_code==201, created.text
    sid=created.json()['data']['id']
    claimed=await c.post(f'/customer-service/sessions/{sid}/claim',headers=agent); assert claimed.status_code==200, claimed.text
    await c.post(f'/customer-service/sessions/{sid}/heartbeat',headers=agent)
    old=datetime.now(timezone.utc)-timedelta(seconds=120)
    async with db.engine.begin() as conn:
      await conn.execute(text('UPDATE customer_service_session SET agent_last_heartbeat_at=:old WHERE id=:sid'), {'old':old,'sid':sid})
    settings=get_settings()
    settings.customer_service_heartbeat_timeout_seconds=30
    data=await run_timeout_sweep(db, CustomerServiceTimeoutService(), settings)
    assert sid in data['session_ids'], data
    async with db.engine.connect() as conn:
      row=(await conn.execute(text('SELECT status,assigned_agent_id,agent_last_heartbeat_at FROM customer_service_session WHERE id=:sid'),{'sid':sid})).mappings().one()
    assert row['status']=='paused' and row['assigned_agent_id'] is None and row['agent_last_heartbeat_at'] is None, dict(row)
    print({'worker_run':True,'sweep_paused':True,'assignment_cleared':True,'pass':True})
 finally:
  if sid:
   async with db.engine.begin() as conn:
    await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='customer_service_session' AND resource_id=:sid"),{'sid':sid})
    await conn.execute(text('DELETE FROM customer_service_session WHERE id=:sid'),{'sid':sid})
  await db.dispose()
asyncio.run(main())
