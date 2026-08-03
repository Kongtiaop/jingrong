from __future__ import annotations
import argparse, asyncio, json, uuid
from decimal import Decimal
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); ap.add_argument('--password',default='Demo@2026Customer'); a=ap.parse_args(); s=get_settings(); pg=Database(s); idem='HTTP-IDEM-'+uuid.uuid4().hex; created=[]
 try:
  async with httpx.AsyncClient(base_url=a.base_url.rstrip('/'),timeout=15) as c:
   login=await c.post('/auth/login',json={'username':'customer_demo','password':a.password}); login.raise_for_status(); token=login.json()['data']['access_token']; h={'Authorization':f'Bearer {token}'}
   async with pg.engine.begin() as db:
    product=str((await db.execute(text("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
   first=await c.post('/trading/orders',headers=h,json={'product_id':product,'amount':'1000.00','idempotency_key':idem}); first_payload=first.json();
   if first.status_code not in (201,200): raise SystemExit(json.dumps({'first':first_payload},ensure_ascii=False))
   first_id=first_payload['data']['id']; created.append(first_id)
   second=await c.post('/trading/orders',headers=h,json={'product_id':product,'amount':'1000.00','idempotency_key':idem}); second_payload=second.json()
   result={'first_status':first.status_code,'second_status':second.status_code,'first_id':first_id,'second_payload':second_payload,'pass':second.status_code in (200,201) and second_payload.get('data',{}).get('id')==first_id}
   print(json.dumps(result,ensure_ascii=False,indent=2));
   if not result['pass']: raise SystemExit(1)
 finally:
  async with pg.engine.begin() as db:
   for oid in created:
    await db.execute(text('DELETE FROM order_status_history WHERE order_id=:id'),{'id':oid}); await db.execute(text('DELETE FROM trades WHERE order_id=:id'),{'id':oid}); await db.execute(text('DELETE FROM orders WHERE id=:id'),{'id':oid})
  await pg.dispose()
if __name__=='__main__': asyncio.run(main())
