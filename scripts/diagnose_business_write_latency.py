from __future__ import annotations
import argparse, asyncio, json
from statistics import quantiles
from time import perf_counter
from uuid import uuid4
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.mysql_profile import MySQLRepository, MySQLTransaction

def stats(vals):
 vals=sorted(vals); p95=quantiles(vals,n=20,method="inclusive")[18] if len(vals)>=20 else vals[-1]; p99=quantiles(vals,n=100,method="inclusive")[98] if len(vals)>=100 else vals[-1]
 return {"p50_ms":round(vals[len(vals)//2],3),"p95_ms":round(p95,3),"p99_ms":round(p99,3),"max_ms":round(vals[-1],3),"samples":len(vals)}
async def pg_worker(db,uid,product,rows):
 acq=[]; execs=[]; commits=[]; ids=[]; t=perf_counter(); conn=await db.engine.connect(); acq.append((perf_counter()-t)*1000)
 trans=await conn.begin()
 try:
  for _ in range(rows):
   ident=str(uuid4()); t=perf_counter(); await conn.exec_driver_sql("INSERT INTO customer_holding (id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status) VALUES ($1,$2,$3,1,100,101,1,1,'inactive')",(ident,uid,product)); execs.append((perf_counter()-t)*1000); ids.append(ident)
  t=perf_counter(); await trans.commit(); commits.append((perf_counter()-t)*1000)
 except: await trans.rollback(); raise
 await conn.close(); return acq,execs,commits,ids
async def mysql_worker(db,uid,product,rows):
 acq=[]; execs=[]; commits=[]; ids=[]; pool=await db.pool(); t=perf_counter(); conn=await pool.acquire(); acq.append((perf_counter()-t)*1000); await conn.begin()
 try:
  async with conn.cursor() as cur:
   for _ in range(rows):
    ident=str(uuid4()); t=perf_counter(); await cur.execute("INSERT INTO customer_holding (id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status) VALUES (%s,%s,%s,1,100,101,1,1,'inactive')",(ident,uid,product)); execs.append((perf_counter()-t)*1000); ids.append(ident)
  t=perf_counter(); await conn.commit(); commits.append((perf_counter()-t)*1000)
 except: await conn.rollback(); raise
 pool.release(conn); return acq,execs,commits,ids
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--workers',type=int,default=10); ap.add_argument('--rows-per-worker',type=int,default=20); a=ap.parse_args(); s=get_settings(); pg=Database(s); mysql=MySQLRepository(s.mysql_url)
 try:
  async with pg.engine.begin() as c: uid=str((await c.exec_driver_sql("SELECT id FROM users WHERE username LIKE 'perf_customer_%' ORDER BY username LIMIT 1")).scalar_one()); product=str((await c.exec_driver_sql("SELECT id FROM product WHERE name='稳健增值计划' LIMIT 1")).scalar_one())
  pool=await mysql.pool()
  async with pool.acquire() as conn:
   async with conn.cursor() as cur: await cur.execute("SELECT id FROM users WHERE username LIKE %s ORDER BY username LIMIT 1",('perf_customer_%',)); uidm=(await cur.fetchone())[0]; await cur.execute("SELECT id FROM product WHERE name=%s LIMIT 1",('稳健增值计划',)); productm=(await cur.fetchone())[0]
  pg_res=await asyncio.gather(*[pg_worker(pg,uid,product,a.rows_per_worker) for _ in range(a.workers)]); mysql_res=await asyncio.gather(*[mysql_worker(mysql,uidm,productm,a.rows_per_worker) for _ in range(a.workers)])
  pg_ids=[x for *_,ids in pg_res for x in ids]; mysql_ids=[x for *_,ids in mysql_res for x in ids]
  async with pg.engine.begin() as c: await c.exec_driver_sql("DELETE FROM customer_holding WHERE id = ANY($1::text[])",(pg_ids,))
  await mysql.execute("DELETE FROM customer_holding WHERE id IN ("+','.join(['%s']*len(mysql_ids))+")",tuple(mysql_ids))
  def pack(res): return {"acquire":stats([x for ac,_,_,_ in res for x in ac]),"execute":stats([x for _,ex,_,_ in res for x in ex]),"commit":stats([x for _,_,co,_ in res for x in co]),"writes":sum(len(ids) for *_,ids in res)}
  result={"config":vars(a),"postgresql":pack(pg_res),"mysql":pack(mysql_res),"pass":len(pg_ids)==a.workers*a.rows_per_worker and len(mysql_ids)==a.workers*a.rows_per_worker}; print(json.dumps(result,ensure_ascii=False,indent=2));
  if not result['pass']: raise SystemExit(1)
 finally: await mysql.close(); await pg.dispose()
if __name__=='__main__': asyncio.run(main())

