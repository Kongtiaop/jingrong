from __future__ import annotations
import argparse, asyncio, json, uuid
from decimal import Decimal
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
def code(r):
 d=r.json().get('detail') or {}; return d.get('code') if isinstance(d,dict) else None
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=ap.parse_args(); db=Database(get_settings()); out=[]
 async with db.engine.begin() as c:
  customer=str((await c.execute(text("select id from users where username='customer_demo'"))).scalar_one()); staff=str((await c.execute(text("select id from users where username='operations_demo'"))).scalar_one()); product=str((await c.execute(text("select id from product where status='active' order by minimum_amount limit 1"))).scalar_one())
 async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'),timeout=20) as c:
  ch=await login(c,'customer_demo','Demo@2026Customer'); sh=await login(c,'operations_demo','Demo@2026Operations')
  # direct order cancel before confirmation
  r=await c.post('/trading/orders',headers=ch,json={'product_id':product,'amount':1000,'idempotency_key':f'matrix-direct-cancel-{uuid.uuid4()}'}); oid=r.json()['data']['id']; r2=await c.post(f'/trading/orders/{oid}/cancel',headers=ch); out.append({'name':'direct_cancel_before_confirmation','status':r2.status_code,'order_status':r2.json().get('data',{}).get('status'),'pass':r2.status_code==200 and r2.json().get('data',{}).get('status')=='cancelled'})
  # direct order reject after confirmation
  r=await c.post('/trading/orders',headers=ch,json={'product_id':product,'amount':1000,'idempotency_key':f'matrix-direct-reject-{uuid.uuid4()}'}); oid=r.json()['data']['id']; await c.post(f'/trading/orders/{oid}/confirm',headers=ch); r2=await c.post(f'/trading/orders/{oid}/reject',headers=sh,json={'note':'matrix reject'}); out.append({'name':'direct_reject_after_confirmation','status':r2.status_code,'order_status':r2.json().get('data',{}).get('status'),'pass':r2.status_code==200 and r2.json().get('data',{}).get('status')=='rejected'})
  # pending review cancel for direct order
  r=await c.post('/trading/orders',headers=ch,json={'product_id':product,'amount':1000,'idempotency_key':f'matrix-direct-pending-cancel-{uuid.uuid4()}'}); oid2=r.json()['data']['id']; await c.post(f'/trading/orders/{oid2}/confirm',headers=ch); r2=await c.post(f'/trading/orders/{oid2}/cancel',headers=ch); out.append({'name':'direct_cancel_after_confirmation','status':r2.status_code,'order_status':r2.json().get('data',{}).get('status'),'pass':r2.status_code==200 and r2.json().get('data',{}).get('status')=='cancelled'})
  # terminal repeat confirm
  r3=await c.post(f'/trading/orders/{oid}/confirm',headers=ch); out.append({'name':'rejected_repeat_confirm','status':r3.status_code,'code':code(r3),'pass':r3.status_code==409})
 print(json.dumps({'checks':out,'pass':all(x['pass'] for x in out)},ensure_ascii=False,indent=2)); raise SystemExit(0 if all(x['pass'] for x in out) else 1)
if __name__=='__main__': asyncio.run(main())
