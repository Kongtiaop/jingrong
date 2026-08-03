from __future__ import annotations
import argparse, asyncio, json, uuid
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
def code(r):
 d=r.json().get('detail') or {}; return d.get('code') if isinstance(d,dict) else None
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=ap.parse_args(); db=Database(get_settings()); checks=[]; auth_id=None; draft_id=None; order_id=None
 try:
  async with db.engine.begin() as c:
   customer=str((await c.execute(text("select id from users where username='customer_demo'"))).scalar_one()); advisor=str((await c.execute(text("select id from users where username='super_admin_demo'"))).scalar_one()); product=str((await c.execute(text("select id from product where status='active' order by minimum_amount limit 1"))).scalar_one())
  async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'),timeout=20) as c:
   ch=await login(c,'customer_demo','Demo@2026Customer'); sh=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
   g=await c.post('/profile/me/advisor-authorizations',headers=ch,json={'advisor_id':advisor,'allowed_actions':'buy','product_scope':'*','max_single_amount':5000,'max_daily_amount':10000,'risk_level_ceiling':'C2','requires_customer_confirmation':True}); g.raise_for_status(); auth_id=g.json()['data']['id']
   # use direct staff draft creation and customer confirm, then reject
   d=await c.post('/profile/staff/operation-drafts',headers=sh,json={'customer_id':customer,'product_id':product,'amount':1000,'note':'reject matrix'}); d.raise_for_status(); draft_id=d.json()['data']['id']
   conf=await c.post(f'/profile/me/operation-drafts/{draft_id}/confirm',headers=ch); conf.raise_for_status()
   sub=await c.post(f'/profile/me/operation-drafts/{draft_id}/submit-order',headers=ch); sub.raise_for_status(); order_id=sub.json()['data']['order_id']
   before=(await c.get('/trading/account/me',headers=ch)).json()['data']
   rej=await c.post(f'/trading/orders/{order_id}/reject',headers=sh,json={'note':'advisor reject matrix'}); checks.append({'name':'advisor_reject','status':rej.status_code,'order_status':rej.json().get('data',{}).get('status'),'pass':rej.status_code==200 and rej.json().get('data',{}).get('status')=='rejected'})
   after=(await c.get('/trading/account/me',headers=ch)).json()['data']; checks.append({'name':'advisor_reject_no_negative_or_refund','available_before':before['available_balance'],'available_after':after['available_balance'],'frozen_after':after['frozen_balance'],'pass':float(after['frozen_balance'])>=0 and float(after['available_balance'])==float(before['available_balance'])})
   repeat=await c.post(f'/trading/orders/{order_id}/reject',headers=sh,json={'note':'repeat'}); checks.append({'name':'advisor_repeat_reject','status':repeat.status_code,'code':code(repeat),'pass':repeat.status_code==409 and code(repeat)=='ORDER_STATE_CONFLICT'})
  print(json.dumps({'checks':checks,'pass':all(x['pass'] for x in checks)},ensure_ascii=False,indent=2)); raise SystemExit(0 if all(x['pass'] for x in checks) else 1)
 finally:
  async with db.engine.begin() as c:
   if order_id:
    await c.execute(text('delete from money_flow where order_id=:id'),{'id':order_id}); await c.execute(text('delete from order_status_history where order_id=:id'),{'id':order_id}); await c.execute(text('delete from orders where id=:id'),{'id':order_id})
   if draft_id: await c.execute(text('delete from advisor_operation_draft where id=:id'),{'id':draft_id})
   if auth_id: await c.execute(text('delete from customer_advisor_authorization where id=:id'),{'id':auth_id})
  await db.dispose()
if __name__=='__main__': asyncio.run(main())
