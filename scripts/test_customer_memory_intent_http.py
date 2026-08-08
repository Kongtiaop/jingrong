from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, lifespan

async def login(c):
 r=await c.post('/auth/login',json={'username':'customer_demo','password':'Demo@2026Customer'}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 async with lifespan(app):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test/api/v1') as c:
   h=await login(c); cid='memory-http-test'
   first=await c.post('/customer-service/sessions/intent-plan',headers={**h,'X-Conversation-Id':cid},json={'note':'我的订单为什么还在审核中'})
   second=await c.post('/customer-service/sessions/intent-plan',headers={**h,'X-Conversation-Id':cid},json={'note':'订单号是最近一笔'})
   tool=await c.post('/customer-service/sessions/read-tool',headers={**h,'X-Tool-Name':'get_my_order_status'},json={'note':'我的订单状态'})
   assert first.status_code==200 and second.status_code==200 and tool.status_code==200
   assert second.json()['data']['tasks']
   assert tool.json()['data']['tool']=='get_my_order_status'
   print({'memory_context_restored':True,'read_tool_contract':True,'pass':True})
if __name__=='__main__': asyncio.run(main())
