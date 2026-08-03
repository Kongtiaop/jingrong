from __future__ import annotations
import argparse, asyncio, json, uuid
import httpx
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); ap.add_argument('--password',default='Demo@2026SuperAdmin'); a=ap.parse_args()
 async with httpx.AsyncClient(base_url=a.base_url.rstrip('/'),timeout=15) as c:
  login=await c.post('/auth/login',json={'username':'super_admin_demo','password':a.password}); login.raise_for_status(); h={'Authorization':f"Bearer {login.json()['data']['access_token']}"}; missing=str(uuid.uuid4()); payload={'name':'不存在产品','product_type':'fund','risk_level':'C1','term_days':30,'minimum_amount':'1','liquidity':'medium','description':'x','target_customer_type':'individual','target_customer_tiers':'ordinary','status':'active'}
  responses=[('update',await c.put(f'/profile/staff/products/{missing}',headers=h,json=payload)),('delete',await c.delete(f'/profile/staff/products/{missing}',headers=h)),('restore',await c.put(f'/profile/staff/products/{missing}/restore',headers=h))]
  checks=[]
  for name,r in responses:
   detail=r.json().get('detail'); checks.append({'name':name,'status':r.status_code,'detail':detail,'pass':r.status_code==404 and isinstance(detail,dict) and detail.get('code')=='PRODUCT_NOT_FOUND'})
  result={'checks':checks,'pass':all(x['pass'] for x in checks)}; print(json.dumps(result,ensure_ascii=False,indent=2));
  if not result['pass']: raise SystemExit(1)
if __name__=='__main__': asyncio.run(main())
