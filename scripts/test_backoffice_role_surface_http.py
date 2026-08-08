from __future__ import annotations
import asyncio, json
import httpx

BASE='http://127.0.0.1:8010/api/v1'
ACCOUNTS={
 'service':('customer_service_demo','Demo@2026CustomerService'),
 'operations':('operations_demo','Demo@2026Operations'),
 'customer':('customer_demo','Demo@2026Customer'),
 'admin':('super_admin_demo','Demo@2026SuperAdmin'),
}
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
 async with httpx.AsyncClient(base_url=BASE,timeout=30) as c:
  hs={k:await login(c,*v) for k,v in ACCOUNTS.items()}
  paths={
   'dashboard':'/admin/dashboard/risk-workbench','admin_users':'/admin/users?limit=1','admin_audit':'/admin/audit-logs?limit=1','enterprise':'/admin/enterprise-verifications?limit=1','recycle':'/admin/recycle-bin?limit=1',
   'staff_customers':'/profile/staff/customers?limit=1','advisor_customers':'/profile/staff/advisor-customers?limit=1','risk':'/risk-alerts?limit=1','aml_batches':'/risk-alerts/batches?limit=1','pending':'/operations/staff/pending?limit=1','operation_audit':'/operations/audit-logs?limit=1','my_ops':'/operations/me?limit=1',
  }
  expected={
   'service':{'dashboard':403,'admin_users':403,'admin_audit':403,'enterprise':403,'recycle':403,'staff_customers':200,'advisor_customers':403,'risk':403,'aml_batches':403,'pending':403,'operation_audit':403,'my_ops':403},
   'operations':{'dashboard':200,'admin_users':403,'admin_audit':403,'enterprise':403,'recycle':403,'staff_customers':403,'advisor_customers':403,'risk':403,'aml_batches':403,'pending':200,'operation_audit':200,'my_ops':403},
   'customer':{'dashboard':403,'admin_users':403,'admin_audit':403,'enterprise':403,'recycle':403,'staff_customers':403,'advisor_customers':403,'risk':403,'aml_batches':403,'pending':403,'operation_audit':403,'my_ops':200},
   'admin':{k:(403 if k=='my_ops' else 200) for k in paths},
  }
  checks=[]
  for role,headers in hs.items():
   for name,path in paths.items():
    r=await c.get(path,headers=headers); ok=r.status_code==expected[role][name]
    checks.append({'role':role,'endpoint':name,'status':r.status_code,'expected':expected[role][name],'pass':ok})
  assert all(x['pass'] for x in checks), [x for x in checks if not x['pass']]
  print(json.dumps({'checks':checks,'pass':True},ensure_ascii=False,indent=2))
if __name__=='__main__': asyncio.run(main())
