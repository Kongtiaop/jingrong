from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.trading import Order, Trade

async def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--base-url',default='http://127.0.0.1:8010/api/v1'); parser.add_argument('--username',default='customer_demo'); parser.add_argument('--password',default='Demo@2026Customer'); args=parser.parse_args()
    settings=get_settings(); db=Database(settings)
    try:
        async with db.session_factory() as s:
            u=(await s.execute(select(User).where(User.username==args.username))).scalar_one_or_none()
            pg_orders=[] if u is None else list((await s.execute(select(Order).where(Order.user_id==u.id).order_by(Order.created_at.desc()))).scalars().all())
            pg_trades=[] if u is None else list((await s.execute(select(Trade).where(Trade.user_id==u.id))).scalars().all())
        async with httpx.AsyncClient(base_url=args.base_url,timeout=10) as c:
            login=await c.post('/auth/login',json={'username':args.username,'password':args.password})
            login.raise_for_status(); token=login.json()['data']['access_token']; h={'Authorization':f'Bearer {token}'}
            orders=await c.get('/trading/orders/me',headers=h); trades=await c.get('/trading/trades/me',headers=h)
            orders.raise_for_status(); trades.raise_for_status()
        print(json.dumps({'script_database_url':settings.database_url,'postgres_direct':{'orders':[str(o.id) for o in pg_orders],'trades':[str(t.id) for t in pg_trades]},'api':{'orders':[x['id'] for x in orders.json()['data']],'trades':[x['id'] for x in trades.json()['data']]},'match':{ 'orders':set(str(o.id) for o in pg_orders)==set(x['id'] for x in orders.json()['data']), 'trades':set(str(t.id) for t in pg_trades)==set(x['id'] for x in trades.json()['data'])}},ensure_ascii=False,indent=2))
    finally: await db.dispose()
if __name__=='__main__': asyncio.run(main())
