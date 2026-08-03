from __future__ import annotations
import argparse, asyncio, json, uuid
import httpx

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); ap.add_argument('--password',default='Demo@2026Customer'); a=ap.parse_args()
    async with httpx.AsyncClient(base_url=a.base_url.rstrip('/'),timeout=15) as c:
        login=await c.post('/auth/login',json={'username':'customer_demo','password':a.password}); login.raise_for_status(); token=login.json()['data']['access_token']; h={'Authorization':f'Bearer {token}'}
        missing=str(uuid.uuid4()); r1=await c.post(f'/trading/orders/{missing}/confirm',headers=h)
        r2=await c.post(f'/trading/orders/{missing}/cancel',headers=h)
        checks=[]
        for name,r,code in [('missing_confirm',r1,'ORDER_NOT_FOUND'),('missing_cancel',r2,'ORDER_NOT_FOUND')]:
            payload=r.json(); detail=payload.get('detail'); ok=r.status_code==404 and isinstance(detail,dict) and detail.get('code')==code
            checks.append({'name':name,'status':r.status_code,'detail':detail,'pass':ok})
        result={'base_url':a.base_url,'checks':checks,'pass':all(x['pass'] for x in checks)}; print(json.dumps(result,ensure_ascii=False,indent=2))
        if not result['pass']: raise SystemExit(1)
if __name__=='__main__': asyncio.run(main())
