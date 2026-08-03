from __future__ import annotations
import argparse, asyncio, json, uuid
from decimal import Decimal
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); ap.add_argument('--requests',type=int,default=5); ap.add_argument('--password',default='Demo@2026Customer'); a=ap.parse_args(); s=get_settings(); pg=Database(s); idem='RACE-'+uuid.uuid4().hex; ids=[]
 try:
  async with pg.engine.begin() as db: product=str((await db.execute(text("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
  async with httpx.AsyncClient(base_url=a.base_url.rstrip('/'),timeout=20) as c:
   login=await c.post('/auth/login',json={'username':'customer_demo','password':a.password}); login.raise_for_status(); token=login.json()['data']['access_token']; h={'Authorization':f'Bearer {token}'}
   async def send():
    r=await c.post('/trading/orders',headers=h,json={'product_id':product,'amount':'1000.00','idempotency_key':idem});
    try: payload=r.json()
    except Exception: payload={'raw':r.text}
    return {'status':r.status_code,'payload':payload}
   responses=await asyncio.gather(*[send() for _ in range(a.requests)])
  async with pg.engine.begin() as db:
   rows=(await db.execute(text("SELECT id,status FROM orders WHERE user_id=(SELECT id FROM users WHERE username='customer_demo') AND idempotency_key=:k"),{'k':idem})).mappings().all(); ids=[str(x['id']) for x in rows]
  statuses=[r['status'] for r in responses]; returned=[r.get('payload',{}).get('data',{}).get('id') for r in responses if isinstance(r.get('payload'),dict)]
  result={'requests':a.requests,'statuses':statuses,'db_order_count':len(rows),'db_order_ids':ids,'returned_ids':returned,'pass':len(rows)==1 and all(x in (200,201,409) for x in statuses)}; print(json.dumps(result,ensure_ascii=False,indent=2));
  if not result['pass']: raise SystemExit(1)
 finally:
  async with pg.engine.begin() as db:
   for oid in ids:
    await db.execute(text('DELETE FROM order_status_history WHERE order_id=:id'),{'id':oid}); await db.execute(text('DELETE FROM trades WHERE order_id=:id'),{'id':oid}); await db.execute(text('DELETE FROM orders WHERE id=:id'),{'id':oid})
  await pg.dispose()
if __name__=='__main__': asyncio.run(main())
