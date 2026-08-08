from __future__ import annotations
import asyncio, sys, uuid
from pathlib import Path
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app, lifespan
async def login(c):
 r=await c.post('/auth/login',json={'username':'customer_demo','password':'Demo@2026Customer'});r.raise_for_status();return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 async with lifespan(app):
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test/api/v1') as c:
   h=await login(c); cid='controlled-op-'+str(uuid.uuid4())
   response=await c.post('/customer-service/sessions/execute-plan',headers={**h,'X-Conversation-Id':cid},json={'note':'帮我查订单状态，顺便帮我赎回'})
   assert response.status_code==200,response.text
   data=response.json()['data']; results={x['intent']:x for x in data['results']}
   assert results['order_status']['status'] in {'succeeded','needs_clarification'}
   assert results['business_operation']['status']=='needs_confirmation'
   assert results['business_operation']['data']['executed'] is False
   assert results['business_operation']['data']['confirmation_required'] is True
   assert data['overall_status'] in {'partially_completed','needs_clarification'}
   print({'read_task_preserved':True,'operation_needs_confirmation':True,'operation_not_executed':True,'pass':True})
if __name__=='__main__':asyncio.run(main())
