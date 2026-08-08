from __future__ import annotations
import asyncio, sys, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
from sqlalchemy import text
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.settings import get_settings
from app.db.session import Database
from app.main import app, lifespan
BASE='http://test/api/v1'
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 db=Database(get_settings()); sid=None
 try:
  async with lifespan(app):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=BASE) as c:
    customer=await login(c,'customer_demo','Demo@2026Customer')
    agent=await login(c,'customer_service_demo','Demo@2026CustomerService')
    admin=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
    created=await c.post('/customer-service/sessions',headers=customer,json={'note':'heartbeat_timeout_test'})
    assert created.status_code==201, created.text
    sid=created.json()['data']['id']
    claimed=await c.post(f'/customer-service/sessions/{sid}/claim',headers=agent)
    assert claimed.status_code==200, claimed.text
    hb=await c.post(f'/customer-service/sessions/{sid}/heartbeat',headers=agent)
    assert hb.status_code==200, hb.text
    async with db.engine.begin() as conn:
     await conn.execute(text("UPDATE customer_service_session SET agent_last_heartbeat_at=:old WHERE id=:sid"), {'old':datetime.now(timezone.utc)-timedelta(seconds=120),'sid':sid})
    sweep=await c.post('/customer-service/sessions/sweep-timeouts?timeout_seconds=30',headers=admin)
    assert sweep.status_code==200, sweep.text
    data=sweep.json()['data']
    assert sid in data['session_ids'], data
    row=(await db.engine.connect()).execute if False else None
    async with db.engine.connect() as conn:
     result=(await conn.execute(text('SELECT status, assigned_agent_id, agent_last_heartbeat_at FROM customer_service_session WHERE id=:sid'),{'sid':sid})).mappings().one()
    assert result['status']=='paused' and result['assigned_agent_id'] is None and result['agent_last_heartbeat_at'] is None, dict(result)
    reclaimed=await c.post(f'/customer-service/sessions/{sid}/claim',headers=agent)
    assert reclaimed.status_code==200, reclaimed.text
    assert reclaimed.json()['data']['status']=='human_active'
    print({'created_human_active':True,'heartbeat_aged':True,'sweep_paused':True,'assignment_cleared':True,'reclaim_after_timeout':True,'pass':True})
 finally:
  if sid:
   async with db.engine.begin() as conn:
    await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='customer_service_session' AND resource_id=:sid"),{'sid':sid})
    await conn.execute(text('DELETE FROM customer_service_session WHERE id=:sid'),{'sid':sid})
  await db.dispose()
asyncio.run(main())
