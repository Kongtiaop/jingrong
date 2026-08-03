from __future__ import annotations
import argparse, asyncio, json, uuid
from decimal import Decimal
import httpx
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database

async def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); ap.add_argument('--password',default='Demo@2026Customer'); a=ap.parse_args()
    s=get_settings(); pg=Database(s); oid=str(uuid.uuid4()); created=False
    try:
        async with pg.engine.begin() as c:
            user=str((await c.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one())
            account=str((await c.execute(text("SELECT id FROM account WHERE user_id=:u"),{'u':user})).scalar_one())
            product=str((await c.execute(text("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1"))).scalar_one())
            await c.execute(text("INSERT INTO orders (id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key,order_source,confirmation_mode) VALUES (:id,:no,:u,:a,:p,1,1,'pending_review','buy','','',:idem,'customer_direct','customer_order')"),{'id':oid,'no':'HTTP-CONFLICT-'+oid[:10],'u':user,'a':account,'p':product,'idem':'http-conflict-'+oid}); created=True
        async with httpx.AsyncClient(base_url=a.base_url.rstrip('/'),timeout=15) as c:
            login=await c.post('/auth/login',json={'username':'customer_demo','password':a.password}); login.raise_for_status(); token=login.json()['data']['access_token']
            r=await c.post(f'/trading/orders/{oid}/confirm',headers={'Authorization':f'Bearer {token}'})
            payload=r.json(); detail=payload.get('detail'); result={'status':r.status_code,'detail':detail,'pass':r.status_code==409 and isinstance(detail,dict) and detail.get('code')=='ORDER_STATE_CONFLICT'}; print(json.dumps(result,ensure_ascii=False,indent=2))
            if not result['pass']: raise SystemExit(1)
    finally:
        if created:
            async with pg.engine.begin() as c:
                await c.execute(text('DELETE FROM money_flow WHERE order_id=:id'),{'id':oid}); await c.execute(text('DELETE FROM order_status_history WHERE order_id=:id'),{'id':oid}); await c.execute(text('DELETE FROM trades WHERE order_id=:id'),{'id':oid}); await c.execute(text('DELETE FROM orders WHERE id=:id'),{'id':oid})
        await pg.dispose()
if __name__=='__main__': asyncio.run(main())




