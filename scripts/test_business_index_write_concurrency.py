from __future__ import annotations
import argparse, asyncio, json
from statistics import quantiles
from time import perf_counter
from uuid import uuid4
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.mysql_profile import MySQLRepository, MySQLTransaction

def stats(vals):
 vals=sorted(vals); p95=quantiles(vals,n=20,method='inclusive')[18] if len(vals)>=20 else vals[-1]; p99=quantiles(vals,n=100,method='inclusive')[98] if len(vals)>=100 else vals[-1]
 return {'p50_ms':round(vals[len(vals)//2],3),'p95_ms':round(p95,3),'p99_ms':round(p99,3),'max_ms':round(vals[-1],3),'samples':len(vals)}
async def pg_worker(db, uid, product, rows):
 vals=[]; ids=[]
 async with db.engine.begin() as c:
  for _ in range(rows):
   ident=str(uuid4()); t=perf_counter(); await c.exec_driver_sql("INSERT INTO customer_holding (id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status) VALUES ($1,$2,$3,1,100,101,1,1,'inactive')",(ident,uid,product)); vals.append((perf_counter()-t)*1000); ids.append(ident)
 return vals,ids
async def mysql_worker(db, uid, product, rows):
 vals=[]; ids=[]
 async with MySQLTransaction(db) as tx:
  for _ in range(rows):
   ident=str(uuid4()); t=perf_counter(); await tx.execute("INSERT INTO customer_holding (id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status) VALUES (%s,%s,%s,1,100,101,1,1,'inactive')",(ident,uid,product)); vals.append((perf_counter()-t)*1000); ids.append(ident)
 return vals,ids
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--workers',type=int,default=10); ap.add_argument('--rows-per-worker',type=int,default=20); ap.add_argument('--rounds',type=int,default=5); a=ap.parse_args(); s=get_settings(); pg=Database(s); mysql=MySQLRepository(s.mysql_url); all_result={"config":vars(a),"rounds":[]}
 try:
  async with pg.engine.begin() as c:
   uid=str((await c.exec_driver_sql("SELECT id FROM users WHERE username LIKE 'perf_customer_%' ORDER BY username LIMIT 1")).scalar_one()); product=str((await c.exec_driver_sql("SELECT id FROM product WHERE name='稳健增值计划' LIMIT 1")).scalar_one())
  pool=await mysql.pool()
  async with pool.acquire() as conn:
   async with conn.cursor() as cur:
    await cur.execute("SELECT id FROM users WHERE username LIKE %s ORDER BY username LIMIT 1",('perf_customer_%',)); uidm=(await cur.fetchone())[0]
    await cur.execute("SELECT id FROM product WHERE name=%s LIMIT 1",('稳健增值计划',)); productm=(await cur.fetchone())[0]
  for r in range(a.rounds):
   t=perf_counter(); pg_results=await asyncio.gather(*[pg_worker(pg,uid,product,a.rows_per_worker) for _ in range(a.workers)]); pg_elapsed=(perf_counter()-t)*1000; pg_ids=[x for _,ids in pg_results for x in ids]
   async with pg.engine.begin() as c: await c.exec_driver_sql("DELETE FROM customer_holding WHERE id = ANY($1::text[])",(pg_ids,))
   t=perf_counter(); mysql_results=await asyncio.gather(*[mysql_worker(mysql,uidm,productm,a.rows_per_worker) for _ in range(a.workers)]); mysql_elapsed=(perf_counter()-t)*1000; mysql_ids=[x for _,ids in mysql_results for x in ids]
   await mysql.execute("DELETE FROM customer_holding WHERE id IN ("+','.join(['%s']*len(mysql_ids))+")",tuple(mysql_ids))
   pg_vals=[v for vals,_ in pg_results for v in vals]; mysql_vals=[v for vals,_ in mysql_results for v in vals]
   all_result["rounds"].append({"round":r+1,"postgresql":{**stats(pg_vals),"total_ms":round(pg_elapsed,3),"writes":len(pg_ids)},"mysql":{**stats(mysql_vals),"total_ms":round(mysql_elapsed,3),"writes":len(mysql_ids)}})
  all_result['pass']=all(x['postgresql']['writes']==a.workers*a.rows_per_worker and x['mysql']['writes']==a.workers*a.rows_per_worker for x in all_result['rounds']); print(json.dumps(all_result,ensure_ascii=False,indent=2))
  if not all_result['pass']: raise SystemExit(1)
 finally: await mysql.close(); await pg.dispose()
if __name__=='__main__': asyncio.run(main())



