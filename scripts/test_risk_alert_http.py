from __future__ import annotations
import asyncio, json, uuid
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 db=Database(get_settings()); order_id=None; alert_ids=[]
 try:
  async with db.engine.begin() as conn:
   customer=str((await conn.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
   product=str((await conn.execute(text("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
  async with httpx.AsyncClient(base_url='http://127.0.0.1:8010/api/v1',timeout=20) as c:
   ch=await login(c,'customer_demo','Demo@2026Customer'); ah=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
   order=await c.post('/trading/orders',headers=ch,json={'product_id':product,'amount':'50000.00','idempotency_key':f'risk-alert-{uuid.uuid4()}'})
   order.raise_for_status(); order_id=order.json()['data']['id']
   await c.post(f'/trading/orders/{order_id}/confirm',headers=ch)
   reviewed=await c.post(f'/trading/orders/{order_id}/review',headers=ah,json={'note':'risk alert smoke'})
   reviewed.raise_for_status()
   alerts=await c.get('/risk-alerts?customer_id='+customer,headers=ah); alerts.raise_for_status(); items=alerts.json()['data']['items']; alert_ids=[x['id'] for x in items if x.get('order_id')==order_id]
   first=items[0] if alert_ids else {}
   handled=await c.post(f"/risk-alerts/{first.get('id')}/handle",headers=ah,json={'status':'acknowledged','note':'人工已接单'}) if first else None
   if handled: handled.raise_for_status()
   result={'alerts_status':alerts.status_code,'alert_count':len(alert_ids),'rule_codes':[x.get('rule_code') for x in items if x.get('order_id')==order_id],'handled_status':handled.json()['data']['status'] if handled else None,'pass':bool(alert_ids) and bool(handled) and handled.json()['data']['status']=='acknowledged'}
   print(json.dumps(result,ensure_ascii=False,indent=2));
   if not result['pass']: raise SystemExit(1)
 finally:
  async with db.engine.begin() as conn:
   if alert_ids: await conn.execute(text('DELETE FROM risk_alert WHERE id = ANY(:ids)'),{'ids':alert_ids})
   if order_id:
    await conn.execute(text('DELETE FROM order_status_history WHERE order_id=:id'),{'id':order_id}); await conn.execute(text('DELETE FROM trades WHERE order_id=:id'),{'id':order_id}); await conn.execute(text('DELETE FROM orders WHERE id=:id'),{'id':order_id})
  await db.dispose()
if __name__=='__main__': asyncio.run(main())

