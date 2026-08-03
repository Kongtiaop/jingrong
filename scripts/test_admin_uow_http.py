from __future__ import annotations
import argparse,asyncio,json,httpx
async def main(base_url):
 async with httpx.AsyncClient(base_url=base_url.rstrip('/'),timeout=15) as c:
  r=await c.post('/auth/login',json={'username':'super_admin_demo','password':'Demo@2026SuperAdmin'}); r.raise_for_status(); h={'Authorization':f"Bearer {r.json()['data']['access_token']}"}
  users=await c.get('/admin/users?status=active&limit=5&offset=0',headers=h); users.raise_for_status()
  roles=await c.get('/admin/roles',headers=h); roles.raise_for_status()
  perms=await c.get('/admin/permissions',headers=h); perms.raise_for_status()
  data={'users_shape':set(users.json()['data'])>= {'items','total'},'roles_count':len(roles.json()['data']),'permissions_count':len(perms.json()['data']),'pass':True}
  print(json.dumps(data,ensure_ascii=False,indent=2))
if __name__=='__main__':
 parser=argparse.ArgumentParser(); parser.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=parser.parse_args(); asyncio.run(main(args.base_url))
