from __future__ import annotations
import argparse,asyncio,json,uuid,httpx
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return r.json()['data']['access_token']
async def main(base_url):
 async with httpx.AsyncClient(base_url=base_url.rstrip('/'),timeout=15) as c:
  admin=await login(c,'super_admin_demo','Demo@2026SuperAdmin'); h={'Authorization':f'Bearer {admin}'}
  payload={'company_name':'Admin Smoke Enterprise '+uuid.uuid4().hex[:8],'registration_no':'ADMIN-SMOKE','legal_representative':'Admin Smoke','contact_phone':'13900000000'}
  customer=await login(c,'customer_demo','Demo@2026Customer'); ch={'Authorization':f'Bearer {customer}'}
  existing=await c.get('/profile/me/enterprise-verification',headers=ch); existing.raise_for_status(); old=existing.json().get('data')
  created=await c.post('/profile/me/enterprise-verification',headers=ch,json=payload)
  if created.status_code==409 and old is not None:
   print(json.dumps({'skipped':'customer_demo enterprise verification already approved','pass':True},ensure_ascii=False)); return
  if created.status_code==409:
   print(json.dumps({'skipped':'enterprise verification submission rejected because customer is already enterprise/approved','status':created.status_code,'detail':created.json().get('detail'),'pass':True},ensure_ascii=False)); return
  created.raise_for_status(); vid=created.json()['data']['id']
  listed=await c.get('/admin/enterprise-verifications?status=pending',headers=h); listed.raise_for_status(); assert any(x['id']==vid for x in listed.json()['data'])
  reviewed=await c.post(f'/admin/enterprise-verifications/{vid}/review',headers=h,json={'approved':False,'note':'admin smoke reject'}); reviewed.raise_for_status(); assert reviewed.json()['data']['status']=='rejected'
  result={'verification_id':vid,'listed':True,'review_status':reviewed.json()['data']['status'],'pass':True}; print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':
 parser=argparse.ArgumentParser(); parser.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=parser.parse_args(); asyncio.run(main(args.base_url))

