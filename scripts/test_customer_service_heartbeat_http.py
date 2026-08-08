from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import app,lifespan
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p});r.raise_for_status();return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 async with lifespan(app):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test/api/v1') as c:
   customer=await login(c,'customer_demo','Demo@2026Customer');agent=await login(c,'customer_service_e2e','Demo@2026CustomerService');admin=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
   created=await c.post('/customer-service/sessions',headers=customer,json={'note':'heartbeat_test'});assert created.status_code==201;sid=created.json()['data']['id']
   claimed=await c.post(f'/customer-service/sessions/{sid}/claim',headers=agent);assert claimed.status_code==200
   hb=await c.post(f'/customer-service/sessions/{sid}/heartbeat',headers=agent);assert hb.status_code==200 and hb.json()['data']['agent_last_heartbeat_at']
   sweep=await c.post(f'/customer-service/sessions/sweep-timeouts?timeout_seconds=30',headers=admin);assert sweep.status_code==200
   # Current heartbeat is fresh, so it should not be paused.
   assert sid not in sweep.json()['data']['session_ids']
   print({'claim':True,'heartbeat':True,'fresh_not_paused':True,'pass':True})
if __name__=='__main__':asyncio.run(main())
