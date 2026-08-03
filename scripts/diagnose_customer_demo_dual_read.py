from __future__ import annotations
import asyncio,json
from decimal import Decimal
import aiomysql
from sqlalchemy import select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.profile import CustomerAssetSnapshot,CustomerHolding
from app.models.trading import Account,Order,Trade,OrderStatusHistory

async def main():
 settings=get_settings(); db=Database(settings); conn=await aiomysql.connect(host='127.0.0.1',port=3307,user='wealth',password='wealth_dev',db='wealth_manager',autocommit=True)
 try:
  async with db.session_factory() as s:
   user=(await s.execute(select(__import__('app.models.auth',fromlist=['User']).User).where(__import__('app.models.auth',fromlist=['User']).User.username=='customer_demo'))).scalar_one()
   uid=user.id
   pg={}
   for name,model in [('account',Account),('orders',Order),('trades',Trade),('status_history',OrderStatusHistory),('holdings',CustomerHolding),('assets',CustomerAssetSnapshot)]:
    if name=='account': rows=list((await s.execute(select(model).where(model.user_id==uid))).scalars().all())
    elif name=='orders': rows=list((await s.execute(select(model).where(model.user_id==uid).order_by(model.created_at))).scalars().all())
    elif name=='trades': rows=list((await s.execute(select(model).where(model.user_id==uid))).scalars().all())
    elif name=='status_history': rows=list((await s.execute(select(model).where(model.order_id.in_(select(Order.id).where(Order.user_id==uid))))).scalars().all())
    elif name=='holdings': rows=list((await s.execute(select(model).where(model.user_id==uid))).scalars().all())
    else: rows=list((await s.execute(select(model).where(model.user_id==uid).order_by(model.snapshot_time))).scalars().all())
    pg[name]=[{'id':str(getattr(r,'id',None)),'amount':str(getattr(r,'amount',None)),'market_value':str(getattr(r,'market_value',None)),'available_balance':str(getattr(r,'available_balance',None)),'total_asset':str(getattr(r,'total_asset',None)),'investable_asset':str(getattr(r,'investable_asset',None)),'quantity':str(getattr(r,'quantity',None)),'status':str(getattr(r,'status',None))} for r in rows]
  async with conn.cursor(aiomysql.DictCursor) as c:
   await c.execute("SELECT id FROM users WHERE username='customer_demo'"); u=await c.fetchone(); uid=u['id']; mysql={}
   queries={'account':'SELECT * FROM account WHERE user_id=%s','orders':'SELECT * FROM orders WHERE user_id=%s ORDER BY created_at','trades':'SELECT * FROM trades WHERE user_id=%s','holdings':'SELECT * FROM customer_holding WHERE user_id=%s','assets':'SELECT * FROM customer_asset_snapshot WHERE user_id=%s ORDER BY snapshot_time'}
   for name,q in queries.items():
    await c.execute(q,(uid,)); rows=await c.fetchall(); mysql[name]=[{'id':str(r.get('id')),'amount':str(r.get('amount')),'market_value':str(r.get('market_value')),'available_balance':str(r.get('available_balance')),'total_asset':str(r.get('total_asset')),'investable_asset':str(r.get('investable_asset')),'quantity':str(r.get('quantity')),'status':str(r.get('status'))} for r in rows]
   await c.execute("SELECT osh.* FROM order_status_history osh JOIN orders o ON o.id=osh.order_id WHERE o.user_id=%s",(uid,)); mysql['status_history']=[str(r.get('id'))+':'+str(r.get('to_status')) for r in await c.fetchall()]
  print(json.dumps({'user_id':uid,'postgres':pg,'mysql':mysql},ensure_ascii=False,indent=2,default=str))
 finally: conn.close(); await db.dispose()
if __name__=='__main__': asyncio.run(main())
