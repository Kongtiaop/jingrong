from __future__ import annotations

import argparse
import asyncio
import json
import httpx


async def main(base_url, username, password):
    async with httpx.AsyncClient(base_url=base_url.rstrip('/'), timeout=15) as client:
        login=await client.post('/auth/login',json={'username':username,'password':password}); login.raise_for_status()
        token=login.json()['data']['access_token']; headers={'Authorization':f'Bearer {token}'}
        products=await client.get('/profile/staff/products?status=active&limit=100',headers=headers); products.raise_for_status()
        items=products.json()['data']['items']
        if not items: raise AssertionError('no active product')
        product=items[0]; pid=product['id']
        deleted=await client.delete(f'/profile/staff/products/{pid}',headers=headers); deleted.raise_for_status()
        assert deleted.json()['data']['status']=='deleted'
        restored=await client.put(f'/profile/staff/products/{pid}/restore',headers=headers); restored.raise_for_status()
        assert restored.json()['data']['status']=='active'
        result={'product_id':pid,'delete_status':deleted.json()['data']['status'],'restore_status':restored.json()['data']['status'],'pass':True}
        print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); parser.add_argument('--username',default='super_admin_demo'); parser.add_argument('--password',default='Demo@2026SuperAdmin'); args=parser.parse_args(); asyncio.run(main(args.base_url,args.username,args.password))
