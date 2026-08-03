from __future__ import annotations
import asyncio,json,uuid,httpx
async def main():
 base='http://127.0.0.1:8010/api/v1'
 async with httpx.AsyncClient(base_url=base,timeout=20) as c:
  cr=await c.post('/auth/login',json={'username':'customer_demo','password':'Demo@2026Customer'}); ar=await c.post('/auth/login',json={'username':'super_admin_demo','password':'Demo@2026SuperAdmin'}); cr.raise_for_status(); ar.raise_for_status(); ch={'Authorization':f"Bearer {cr.json()['data']['access_token']}"}; ah={'Authorization':f"Bearer {ar.json()['data']['access_token']}"}
  me=(await c.get('/auth/me',headers=ch)).json()['data']; staff=(await c.get('/profile/staff/advisors',headers=ah)).json()['data']['items'][-1]; await c.post('/profile/me/advisor-authorizations',headers=ch,json={'advisor_id':staff['id'],'allowed_actions':'redeem','product_scope':'*','max_single_amount':50000,'max_daily_amount':100000,'risk_level_ceiling':'C5','requires_customer_confirmation':True})
  p=(await c.get('/profile/me',headers=ch)).json()['data']; h=p['holdings'][0]; key=f'staff-redeem-{uuid.uuid4()}'
  r=await c.post('/operations/staff/redeem',headers=ah,json={'customer_id':me['id'],'product_id':h['product_id'],'quantity':1,'amount':1,'customer_confirmed':True,'idempotency_key':key}); checks=[{'name':'staff_submit','status':r.status_code,'pass':r.status_code==200 and r.json().get('data',{}).get('status')=='pending_review'}]
  if r.status_code==200:
   oid=r.json()['data']['operation_id']; r2=await c.post('/operations/staff/redeem',headers=ah,json={'customer_id':me['id'],'product_id':h['product_id'],'quantity':1,'amount':1,'customer_confirmed':True,'idempotency_key':key}); checks.append({'name':'staff_idempotent','pass':r2.status_code==200 and r2.json().get('data',{}).get('operation_id')==oid}); r3=await c.post(f'/operations/redeem/{oid}/review',headers=ah,json={'approve':True,'note':'staff redeem test'}); checks.append({'name':'staff_review','status':r3.status_code,'pass':r3.status_code==200 and r3.json().get('data',{}).get('status')=='settled'})
  print(json.dumps({'checks':checks,'pass':all(x['pass'] for x in checks)},ensure_ascii=False,indent=2)); raise SystemExit(0 if all(x['pass'] for x in checks) else 1)
if __name__=='__main__': asyncio.run(main())


