from __future__ import annotations
import asyncio,json,uuid
import httpx
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 base='http://127.0.0.1:8010/api/v1'
 async with httpx.AsyncClient(base_url=base,timeout=20) as c:
  ch=await login(c,'customer_demo','Demo@2026Customer'); sh=await login(c,'operations_demo','Demo@2026Operations'); prof=(await c.get('/profile/me',headers=ch)).json()['data']; h=prof['holdings'][0]; before=(await c.get('/trading/account/me',headers=ch)).json()['data']; key=f'redeem-reject-{uuid.uuid4()}'
  r=await c.post('/operations/redeem',headers=ch,json={'product_id':h['product_id'],'quantity':1,'amount':1,'idempotency_key':key}); oid=r.json()['data']['operation_id']; reject=await c.post(f'/operations/redeem/{oid}/review',headers=sh,json={'approve':False,'note':'reject matrix'}); repeat=await c.post(f'/operations/redeem/{oid}/review',headers=sh,json={'approve':True,'note':'repeat'}); after=(await c.get('/trading/account/me',headers=ch)).json()['data']; result={'reject':reject.status_code==200 and reject.json()['data']['status']=='rejected','repeat':repeat.status_code==409,'balance_unchanged':float(after['available_balance'])==float(before['available_balance']) and float(after['frozen_balance'])==float(before['frozen_balance'])}; print(json.dumps(result,ensure_ascii=False,indent=2)); raise SystemExit(0 if all(result.values()) else 1)
if __name__=='__main__': asyncio.run(main())
