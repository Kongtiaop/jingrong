from __future__ import annotations
import asyncio, json
import httpx

BASE_URL='http://127.0.0.1:8010/api/v1'
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 async with httpx.AsyncClient(base_url=BASE_URL,timeout=30) as c:
  customer_service=await login(c,'customer_service_demo','Demo@2026CustomerService')
  operations=await login(c,'operations_demo','Demo@2026Operations')
  customer=await login(c,'customer_demo','Demo@2026Customer')
  admin=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
  advisor=await c.get('/profile/staff/advisor-customers',headers=customer_service)
  service_pending=await c.get('/operations/staff/pending',headers=customer_service)
  service_review=await c.post('/operations/redeem/not-found/review',headers=customer_service,json={'approve':False,'note':'permission test'})
  service_audit=await c.get('/operations/audit-logs',headers=customer_service)
  operations_pending=await c.get('/operations/staff/pending',headers=operations)
  customer_pending=await c.get('/operations/staff/pending',headers=customer)
  admin_advisor=await c.get('/profile/staff/advisor-customers',headers=admin)
  assert advisor.status_code==403, advisor.text
  assert service_pending.status_code==403, service_pending.text
  assert service_review.status_code==403, service_review.text
  assert service_audit.status_code==403, service_audit.text
  assert operations_pending.status_code==200, operations_pending.text
  assert customer_pending.status_code==403, customer_pending.text
  assert admin_advisor.status_code==200, admin_advisor.text
  print(json.dumps({'customer_service_advisor':advisor.status_code,'customer_service_pending':service_pending.status_code,'customer_service_review':service_review.status_code,'customer_service_audit':service_audit.status_code,'operations_pending':operations_pending.status_code,'customer_pending':customer_pending.status_code,'admin_advisor':admin_advisor.status_code,'pass':True},ensure_ascii=False,indent=2))
if __name__=='__main__': asyncio.run(main())
