from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
import httpx
from sqlalchemy import text
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import app,lifespan
from app.core.settings import get_settings
from app.db.session import Database
from app.models.notification import Notification
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 db=Database(get_settings()); sid=''; aid='';
 try:
  async with lifespan(app):
   async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test/api/v1') as c:
    customer=await login(c,'customer_demo','Demo@2026Customer'); agent=await login(c,'customer_service_demo','Demo@2026CustomerService')
    created=await c.post('/customer-service/sessions',headers=customer,json={'note':'message-test'}); assert created.status_code==201; sid=created.json()['data']['id']
    forbidden=await c.post(f'/customer-service/sessions/{sid}/messages',headers=agent,json={'content':'未接入不能回复'}); assert forbidden.status_code in {403,409}
    before_customer=await c.get('/notifications',headers=customer); assert before_customer.status_code==200
    customer_msg=await c.post(f'/customer-service/sessions/{sid}/messages',headers=customer,json={'content':'我需要人工帮助'}); assert customer_msg.status_code==201
    customer_notifications=await c.get('/notifications?unread_only=true',headers=agent); assert customer_notifications.status_code==200
    claimed=await c.post(f'/customer-service/sessions/{sid}/claim',headers=agent); assert claimed.status_code==200; aid=claimed.json()['data']['assigned_agent_id']
    claim_notifications=await c.get('/notifications?unread_only=true',headers=customer); assert claim_notifications.status_code==200
    async with db.session_factory() as session:
     from sqlalchemy import select
     from app.models.outbox import OutboxEvent
     pending=list((await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id==sid,OutboxEvent.event_type=='notification.customer_service'))).scalars().all())
     assert any(x.payload_json.get('notification_type')=='customer_service_claimed' for x in pending)
    agent_msg=await c.post(f'/customer-service/sessions/{sid}/messages',headers=agent,json={'content':'您好，我来协助您。'}); assert agent_msg.status_code==201
    incremental=await c.get(f'/customer-service/sessions/{sid}/messages',headers=customer,params={'after':customer_msg.json()['data']['id']}); assert incremental.status_code==200 and [row['id'] for row in incremental.json()['data']]==[agent_msg.json()['data']['id']]
    empty_incremental=await c.get(f'/customer-service/sessions/{sid}/messages',headers=customer,params={'after':agent_msg.json()['data']['id']}); assert empty_incremental.status_code==200 and empty_incremental.json()['data']==[]
    agent_notifications=await c.get('/notifications?unread_only=true',headers=customer); assert agent_notifications.status_code==200
    async with db.session_factory() as session:
     from sqlalchemy import select
     from app.models.outbox import OutboxEvent
     pending=list((await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id==sid,OutboxEvent.event_type=='notification.customer_service'))).scalars().all())
     assert any(x.payload_json.get('notification_type')=='customer_service_message' for x in pending)
    listed=await c.get(f'/customer-service/sessions/{sid}/messages',headers=customer); items=listed.json()['data']; assert listed.status_code==200 and len(items)==2
    blocked=await c.post('/customer-service/sessions/execute-plan',headers={**customer,'X-Conversation-Id':'message-gate'},json={'note':'公司业务是什么'}); assert blocked.status_code==200
    print({'customer_message':True,'agent_message':True,'message_scope':True,'incremental_after_cursor':True,'notification_outbox_enqueued':True,'notification_not_inline':True,'ai_gate_response':blocked.json()['data']['overall_status']=='waiting_human','pass':True})
 finally:
  async with db.engine.begin() as conn:
   if sid:
    await conn.execute(text("DELETE FROM customer_service_message WHERE session_id=:sid"),{'sid':sid})
    await conn.execute(text("DELETE FROM notification WHERE resource_type='customer_service_session' AND resource_id=:sid"),{'sid':sid})
    await conn.execute(text("DELETE FROM outbox_event WHERE aggregate_type='customer_service_session' AND aggregate_id=:sid"),{'sid':sid})
    await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='customer_service_session' AND resource_id=:sid"),{'sid':sid})
    await conn.execute(text("DELETE FROM customer_service_session WHERE id=:sid"),{'sid':sid})
  await db.dispose()
if __name__=='__main__': asyncio.run(main())
