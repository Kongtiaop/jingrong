from __future__ import annotations
import argparse, asyncio
import httpx

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=ap.parse_args()
    async with httpx.AsyncClient(base_url=args.base_url.rstrip('/'),timeout=20) as c:
        login=await c.post('/auth/login',json={'username':'customer_demo','password':'Demo@2026Customer'}); login.raise_for_status(); h={'Authorization':f"Bearer {login.json()['data']['access_token']}"}
        products=await c.get('/profile/products'); products.raise_for_status(); product=products.json()['data'][0]
        order=await c.post('/trading/orders',headers=h,json={'product_id':product['id'],'amount':max(float(product.get('minimum_amount') or 1000),1000),'idempotency_key':'direct-flow-20260803'})
        status=order.json().get('data',{}).get('status')
        listed=await c.get('/trading/orders/me',headers=h); print({'list_status':listed.status_code,'list_body':listed.text}); listed.raise_for_status(); data=listed.json().get('data',{})
        items=data.get('items',[]) if isinstance(data,dict) else data
        found=any(x.get('id')==order.json().get('data',{}).get('id') for x in items)
        print({'create_status':order.status_code,'created_order_status':status,'listed':found,'pass':order.status_code==201 and status=='pending_confirmation' and found})
        raise SystemExit(0 if order.status_code==201 and status=='pending_confirmation' and found else 1)
if __name__=='__main__': asyncio.run(main())

