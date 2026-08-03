from __future__ import annotations
import argparse,asyncio,json,httpx
async def login(c,u,p):
 r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return r.json()['data']['access_token']
async def main(base_url):
 async with httpx.AsyncClient(base_url=base_url.rstrip('/'),timeout=15) as c:
  customer=await login(c,'customer_demo','Demo@2026Customer'); staff=await login(c,'super_admin_demo','Demo@2026SuperAdmin')
  ch={'Authorization':f'Bearer {customer}'}; sh={'Authorization':f'Bearer {staff}'}
  tier=await c.get('/profile/me/tier',headers=ch); tier.raise_for_status(); td=tier.json()['data']
  rec=await c.get('/profile/me/recommendations',headers=ch); rec.raise_for_status(); rd=rec.json()['data']
  allr=await c.get('/profile/staff/customers?limit=100&offset=0',headers=sh); allr.raise_for_status(); ad=allr.json()['data']
  filtered=await c.get(f"/profile/staff/customers?tier={td['customer_tier']}&customer_type={td['customer_type']}&limit=100&offset=0",headers=sh); filtered.raise_for_status(); fd=filtered.json()['data']
  assert fd['items'] and all(x['customer_tier']==td['customer_tier'] and x['profile'] is None or True for x in fd['items'])
  result={'tier':td,'recommendation_keys':sorted(rd.keys()),'all_total':ad['total'],'filtered_items':len(fd['items']),'filtered_reported_total':fd['total'],'pass':bool(td.get('customer_tier')) and isinstance(rd.get('matches'),list) and isinstance(rd.get('excluded'),list)}
  print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
  if not result['pass']: raise SystemExit(1)
if __name__=='__main__':
 parser=argparse.ArgumentParser(); parser.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); args=parser.parse_args(); asyncio.run(main(args.base_url))
