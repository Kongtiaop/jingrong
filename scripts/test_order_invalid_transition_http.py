from __future__ import annotations
import argparse, asyncio, json, uuid
import httpx
async def login(c):
 r=await c.post('/auth/login',json={'username':'customer_demo','password':'Demo@2026Customer'}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=ap.parse_args(); checks=[]
 async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'),timeout=20) as c:
  h=await login(c); products=(await c.get('/profile/products')).json()['data']; p=products[0]
  r=await c.post('/trading/orders',headers=h,json={'product_id':p['id'],'amount':max(float(p.get('minimum_amount') or 1000),1000),'idempotency_key':f'invalid-transition-{uuid.uuid4()}'}); oid=r.json()['data']['id']
  r=await c.post(f'/trading/orders/{oid}/confirm',headers=h); checks.append({'name':'first_confirm','status':r.status_code,'pass':r.status_code==200})
  r=await c.post(f'/trading/orders/{oid}/confirm',headers=h); detail=r.json().get('detail') or {}; checks.append({'name':'repeat_confirm','status':r.status_code,'code':detail.get('code') if isinstance(detail,dict) else None,'pass':r.status_code==409})
  r=await c.post(f'/trading/orders/{oid}/cancel',headers=h); checks.append({'name':'cancel_after_pending_review','status':r.status_code,'pass':r.status_code==200})
  r=await c.post(f'/trading/orders/{oid}/confirm',headers=h); detail=r.json().get('detail') or {}; checks.append({'name':'confirm_after_cancel','status':r.status_code,'code':detail.get('code') if isinstance(detail,dict) else None,'pass':r.status_code==409})
 print(json.dumps({'checks':checks,'pass':all(x['pass'] for x in checks)},ensure_ascii=False,indent=2)); raise SystemExit(0 if all(x['pass'] for x in checks) else 1)
if __name__=='__main__': asyncio.run(main())
