from __future__ import annotations
import asyncio, json
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import text
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work

async def pg_case(db):
 oid=str(uuid4()); result={"backend":"postgresql","order_id":oid}
 async with db.engine.begin() as c:
  user=str((await c.execute(text("SELECT id FROM users WHERE username='customer_demo'"))).scalar_one()); account=str((await c.execute(text("SELECT id FROM account WHERE user_id=:u"),{"u":user})).scalar_one()); product=str((await c.execute(text("SELECT id FROM product WHERE status='active' ORDER BY name LIMIT 1"))).scalar_one())
  try:
   async with c.begin_nested():
    await c.execute(text("INSERT INTO orders (id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES (:id,:no,:u,:a,:p,1,1,'pending_confirmation','buy','', '',:idem)"),{"id":oid,"no":"ERR-"+oid[:12],"u":user,"a":account,"p":product,"idem":"err-"+oid})
    await c.execute(text("INSERT INTO trades (id,trade_no,order_id,user_id,product_id,amount,quantity) VALUES (:id,:no,:o,:u,:p,1,1)"),{"id":str(uuid4()),"no":"ERR-T-"+oid[:10],"o":oid,"u":user,"p":product})
    raise RuntimeError('forced rollback')
  except RuntimeError: result['rollback_caught']=True
  result['orders_after']=int((await c.execute(text("SELECT count(*) FROM orders WHERE id=:id"),{"id":oid})).scalar_one()); result['trades_after']=int((await c.execute(text("SELECT count(*) FROM trades WHERE order_id=:id"),{"id":oid})).scalar_one()); result['not_found']=result['orders_after']==0
 return result
async def mysql_case(uow):
 oid=str(uuid4()); result={"backend":"mysql","order_id":oid}; db=uow.database; user=await db.fetch_one("SELECT id FROM users WHERE username='customer_demo'"); account=await db.fetch_one("SELECT id FROM account WHERE user_id=%s",(user['id'],)); product=await db.fetch_one("SELECT id FROM product WHERE status='active' ORDER BY name LIMIT 1")
 try:
  async with uow.transaction() as tx:
   await tx.insert_order({'id':oid,'order_no':'ERR-'+oid[:12],'user_id':user['id'],'account_id':account['id'],'product_id':product['id'],'amount':Decimal('1'),'quantity':Decimal('1'),'status':'pending_confirmation','side':'buy','review_note':'','failure_reason':'','idempotency_key':'err-'+oid})
   await tx.insert_trade({'id':str(uuid4()),'trade_no':'ERR-T-'+oid[:10],'order_id':oid,'user_id':user['id'],'product_id':product['id'],'amount':Decimal('1'),'quantity':Decimal('1')})
   raise RuntimeError('forced rollback')
 except RuntimeError: result['rollback_caught']=True
 result['orders_after']=int((await db.fetch_one("SELECT COUNT(*) AS n FROM orders WHERE id=%s",(oid,)))['n']); result['trades_after']=int((await db.fetch_one("SELECT COUNT(*) AS n FROM trades WHERE order_id=%s",(oid,)))['n']); result['not_found']=result['orders_after']==0; return result
async def main():
 s=get_settings(); pg=Database(s); mysql=create_mysql_validation_unit_of_work(s.mysql_url)
 try:
  pg_result=await pg_case(pg); mysql_result=await mysql_case(mysql); result={'postgresql':pg_result,'mysql':mysql_result,'pass':all(x['rollback_caught'] and x['orders_after']==0 and x['trades_after']==0 and x['not_found'] for x in (pg_result,mysql_result))}; print(json.dumps(result,ensure_ascii=False,indent=2,default=str));
  if not result['pass']: raise SystemExit(1)
 finally: await mysql.close(); await pg.dispose()
if __name__=='__main__': asyncio.run(main())

