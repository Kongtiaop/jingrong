import asyncio, json
from decimal import Decimal
import httpx

BASE="http://127.0.0.1:8010/api/v1"
ACCOUNTS={"customer_demo":("customer_demo","Demo@2026Customer"),"hNW_demo":("hNW_demo","Demo@2026HighNetWorth"),"enterprise_demo":("enterprise_demo","Demo@2026Enterprise")}

async def main():
 async with httpx.AsyncClient(base_url=BASE,timeout=20) as c:
  out=[]
  for name,(username,password) in ACCOUNTS.items():
   login=await c.post('/auth/login',json={"username":username,"password":password}); data=login.json().get('data') or {}; token=data.get('access_token'); headers={"Authorization":f"Bearer {token}"} if token else {}
   products=(await c.get('/profile/products',headers=headers)).json().get('data',[]) if token else []
   risk=(await c.get('/profile/me/risk-assessment',headers=headers)).json().get('data') if token else None
   out.append({"account":name,"login":login.status_code,"risk":risk,"products":[{"name":p["name"],"risk":p["risk_level"],"type":p.get("target_customer_type")} for p in products]})
  print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__': asyncio.run(main())
