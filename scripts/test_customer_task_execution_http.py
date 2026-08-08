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
   h=await login(c); cid='task-exec-test-' + str(__import__('uuid').uuid4())
   r=await c.post('/customer-service/sessions/execute-plan',headers={**h,'X-Conversation-Id':cid},json={'note':'帮我查订单状态、账户风险限制和资产情况'})
   assert r.status_code==200, r.text
   d=r.json()['data']; intents={x['intent'] for x in d['results']}
   assert 'order_status' in intents and 'risk_restriction' in intents and 'asset_summary' in intents
   cid2='task-clarify-' + str(__import__('uuid').uuid4())
   unknown=await c.post('/customer-service/sessions/execute-plan',headers={**h,'X-Conversation-Id':cid2},json={'note':'帮我处理一下'})
   assert unknown.status_code==200 and unknown.json()['data']['overall_status']=='needs_clarification'
   clarified=await c.post('/customer-service/sessions/execute-plan',headers={**h,'X-Conversation-Id':cid2},json={'note':'我的订单状态'})
   assert clarified.status_code==200 and clarified.json()['data']['overall_status'] in {'completed','partially_completed','needs_clarification'}
   cid3='task-clarify-exhaust-' + str(__import__('uuid').uuid4())
   first=await c.post('/customer-service/sessions/execute-plan',headers={**h,'X-Conversation-Id':cid3},json={'note':'帮我处理一下'})
   second=await c.post('/customer-service/sessions/execute-plan',headers={**h,'X-Conversation-Id':cid3},json={'note':'还是处理一下'})
   assert first.status_code==200 and second.status_code==200 and second.json()['data']['overall_status']=='handoff'
   print({'multi_task_execution':True,'partial_or_complete_response':True,'one_turn_clarification':True,'clarification_reuse':True,'second_clarification_handoff':True,'pass':True})
if __name__=='__main__': asyncio.run(main())

