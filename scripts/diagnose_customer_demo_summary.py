from __future__ import annotations
import asyncio,json,aiomysql
from sqlalchemy import select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot,CustomerHolding
from app.models.trading import Account
async def main():
 db=Database(get_settings()); conn=await aiomysql.connect(host='127.0.0.1',port=3307,user='wealth',password='wealth_dev',db='wealth_manager',autocommit=True)
 try:
  async with db.session_factory() as s:
   u=(await s.execute(select(User).where(User.username=='customer_demo'))).scalar_one(); uid=u.id
   pacc=(await s.execute(select(Account).where(Account.user_id==uid))).scalar_one(); ph=list((await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==uid))).scalars().all()); pa=list((await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==uid).order_by(CustomerAssetSnapshot.snapshot_time.desc()).limit(5))).scalars().all())
  async with conn.cursor(aiomysql.DictCursor) as c:
   await c.execute("SELECT * FROM account WHERE user_id=%s",(uid,)); ma=await c.fetchone(); await c.execute("SELECT * FROM customer_holding WHERE user_id=%s",(uid,)); mh=await c.fetchall(); await c.execute("SELECT * FROM customer_asset_snapshot WHERE user_id=%s ORDER BY snapshot_time DESC LIMIT 5",(uid,)); massets=await c.fetchall()
  print(json.dumps({'pg_account':{'available':str(pacc.available_balance),'frozen':str(pacc.frozen_balance)},'mysql_account':ma,'pg_holdings':[{'id':h.id,'mv':str(h.market_value),'q':str(h.quantity)} for h in ph],'mysql_holdings':mh,'pg_assets':[{'id':a.id,'investable':str(a.investable_asset),'total':str(a.total_asset)} for a in pa],'mysql_assets':massets},ensure_ascii=False,indent=2,default=str))
 finally: conn.close(); await db.dispose()
if __name__=='__main__': asyncio.run(main())
