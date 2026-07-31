import asyncio,json,uuid,httpx
BASE="http://127.0.0.1:8010/api/v1"
ACCOUNTS={"customer_demo":("customer_demo","Demo@2026Customer"),"hNW_demo":("hNW_demo","Demo@2026HighNetWorth"),"enterprise_demo":("enterprise_demo","Demo@2026Enterprise")}
async def login(c,u,p):
 r=await c.post('/auth/login',json={"username":u,"password":p}); return (r.json().get('data') or {}).get('access_token')
async def main():
 async with httpx.AsyncClient(base_url=BASE,timeout=20) as c:
  tokens={k:await login(c,*v) for k,v in ACCOUNTS.items()}; products=(await c.get('/profile/products')).json()['data']; by={p['name']:p for p in products}; results=[]
  async def scenario(name,product,amount):
   h={"Authorization":f"Bearer {tokens[name]}"}; r=await c.post('/trading/orders',headers=h,json={"product_id":by[product]['id'],"amount":amount,"idempotency_key":str(uuid.uuid4())}); item=(r.json().get('data') or {}); result={"customer":name,"product":product,"create_status":r.status_code,"create_detail":r.json().get('detail'),"confirm_status":None,"confirm_detail":None}
   if r.status_code in (200,201):
    q=await c.post(f"/trading/orders/{item['id']}/confirm",headers=h); result['confirm_status']=q.status_code; result['confirm_detail']=q.json().get('detail'); result['order_status']=(q.json().get('data') or {}).get('status')
   results.append(result)
  await scenario('customer_demo','现金管理保本计划',1000)
  await scenario('customer_demo','稳健增值计划',10000)
  await scenario('customer_demo','私行进取策略',500000)
  await scenario('hNW_demo','成长精选组合',50000)
  await scenario('hNW_demo','私行进取策略',500000)
  await scenario('enterprise_demo','企业现金管理计划',100000)
  await scenario('enterprise_demo','稳健增值计划',10000)
  print(json.dumps(results,ensure_ascii=False,indent=2))
if __name__=='__main__': asyncio.run(main())
