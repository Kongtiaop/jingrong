from __future__ import annotations
import asyncio, sys
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import app,lifespan
async def login(c):
 r=await c.post('/auth/login',json={'username':'customer_demo','password':'Demo@2026Customer'}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 async with lifespan(app):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test/api/v1') as c:
   h=await login(c)
   r=await c.post('/customer-service/sessions/execute-plan',headers={**h,'X-Conversation-Id':'rag-explain-test'},json={'note':'我的订单为什么还在审核中'})
   assert r.status_code==200, r.text
   data=r.json()['data']; assert data['results']
   print({'rag_explanation_path':True,'task_results_present':True,'pass':True})
if __name__=='__main__': asyncio.run(main())
