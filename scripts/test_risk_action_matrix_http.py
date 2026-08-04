from __future__ import annotations
import asyncio,json,uuid
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p});r.raise_for_status();return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
def code(r):
 d=r.json().get('detail') or {};return d.get('code') if isinstance(d,dict) else None
async def main():
 db=Database(get_settings()); aid=oid=None; account_id=None; before_available=before_frozen=None
 try:
  async with db.engine.begin() as conn:
   uid=str((await conn.execute(text("select id from users where username='customer_demo'"))).scalar_one()); pid=str((await conn.execute(text("select id from product where risk_level='C1' order by minimum_amount limit 1"))).scalar_one()); account_id=str((await conn.execute(text('select id from account where user_id=:u'),{'u':uid})).scalar_one())
  async with httpx.AsyncClient(base_url='http://127.0.0.1:8010/api/v1',timeout=30) as c:
   ch=await login(c,'customer_demo','Demo@2026Customer'); ah=await login(c,'super_admin_demo','Demo@2026SuperAdmin');
   async with db.engine.begin() as conn:
    balances=(await conn.execute(text('select available_balance,frozen_balance from account where id=:a'),{'a':account_id})).mappings().one(); before_available=balances['available_balance']; before_frozen=balances['frozen_balance']
   created=await c.post('/trading/orders',headers=ch,json={'product_id':pid,'amount':'1000','idempotency_key':f'action-{uuid.uuid4()}'});created.raise_for_status();oid=created.json()['data']['id'];(await c.post(f'/trading/orders/{oid}/confirm',headers=ch)).raise_for_status()
   async with db.engine.begin() as conn:
    aid=str(uuid.uuid4());await conn.execute(text("insert into risk_alert (id,customer_id,order_id,rule_code,alert_level,status,confidence,evidence_json,recommendation,handled_note,assessment_result,assessment_note,action_status,restriction_status,created_at) values (:id,:u,:o,'RW-TEST','high','acknowledged',1,'{}','test','', 'confirmed_risk','test','not_started','none',now())"),{'id':aid,'u':uid,'o':oid})
   hold=await c.post(f'/risk-alerts/{aid}/action',headers=ah,json={'action_code':'HOLD_ORDER','note':'hold'});hold.raise_for_status();
   async with db.engine.begin() as conn: st=(await conn.execute(text('select status from orders where id=:o'),{'o':oid})).scalar_one(); held=(await conn.execute(text('select available_balance,frozen_balance from account where id=:a'),{'a':account_id})).mappings().one()
   hold2=await c.post(f'/risk-alerts/{aid}/action',headers=ah,json={'action_code':'HOLD_ORDER','note':'hold again'})
   reject=await c.post(f'/risk-alerts/{aid}/action',headers=ah,json={'action_code':'REJECT_ORDER','note':'reject'});reject.raise_for_status()
   async with db.engine.begin() as conn: rows=(await conn.execute(text('select o.status as order_status,a.available_balance,a.frozen_balance from orders o join account a on a.id=o.account_id where o.id=:o'),{'o':oid})).mappings().one(); history=(await conn.execute(text('select from_status,to_status,note from order_status_history where order_id=:o order by created_at,id'),{'o':oid})).mappings().all()
   reject2=await c.post(f'/risk-alerts/{aid}/action',headers=ah,json={'action_code':'REJECT_ORDER','note':'again'})
   result={'hold_status':hold.status_code,'order_after_hold':st,'repeat_hold_status':hold2.status_code,'repeat_hold_code':code(hold2),'reject_status':reject.status_code,'order_after_reject':rows['order_status'],'repeat_reject_status':reject2.status_code,'repeat_reject_code':code(reject2),'available_restored':str(rows['available_balance'])==str(before_available),'frozen_restored':str(rows['frozen_balance'])==str(before_frozen),'hold_preserves_frozen':str(held['frozen_balance'])==str(before_frozen+1000),'before_available':str(before_available),'before_frozen':str(before_frozen),'after_available':str(rows['available_balance']),'after_frozen':str(rows['frozen_balance']),'history': [dict(x) for x in history], 'risk_history_ok': any(x['to_status']=='risk_hold' for x in history) and any(x['to_status']=='rejected' and x['from_status']=='risk_hold' for x in history), 'pass':hold.status_code==200 and st=='risk_hold' and reject.status_code==200 and rows['order_status']=='rejected' and reject2.status_code==409 and str(rows['available_balance'])==str(before_available) and str(rows['frozen_balance'])==str(before_frozen) and any(x['to_status']=='risk_hold' for x in history) and any(x['to_status']=='rejected' and x['from_status']=='risk_hold' for x in history)};print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['pass'] else 1)
 finally:
  async with db.engine.begin() as conn:
   if aid: await conn.execute(text('delete from risk_alert where id=:id'),{'id':aid})
   if oid: await conn.execute(text('delete from order_status_history where order_id=:o'),{'o':oid});await conn.execute(text('delete from orders where id=:o'),{'o':oid})
  await db.dispose()
if __name__=='__main__':asyncio.run(main())




