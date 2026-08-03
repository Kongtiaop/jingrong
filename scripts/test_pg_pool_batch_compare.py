from __future__ import annotations
import argparse, asyncio, json
from statistics import quantiles
from time import perf_counter
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.settings import get_settings

def stats(v):
 v=sorted(v); return {'p50_ms':round(v[len(v)//2],3),'p95_ms':round(quantiles(v,n=20,method='inclusive')[18] if len(v)>=20 else v[-1],3),'p99_ms':round(quantiles(v,n=100,method='inclusive')[98] if len(v)>=100 else v[-1],3),'max_ms':round(v[-1],3),'samples':len(v)}
async def scenario(url,pool_size,max_overflow,workers,rows):
 e=create_async_engine(url,pool_size=pool_size,max_overflow=max_overflow,pool_pre_ping=True); ids=[]; acq=[]; execs=[]; commits=[]
 async with e.begin() as c:
  uid=str((await c.exec_driver_sql("SELECT id FROM users WHERE username LIKE 'perf_customer_%' ORDER BY username LIMIT 1")).scalar_one()); product=str((await c.exec_driver_sql("SELECT id FROM product WHERE name='稳健增值计划' LIMIT 1")).scalar_one())
 async def worker():
  nonlocal ids
  t=perf_counter(); conn=await e.connect(); acq.append((perf_counter()-t)*1000); tx=await conn.begin(); local=[]
  try:
   for _ in range(rows):
    ident=str(uuid4()); t=perf_counter(); await conn.exec_driver_sql("INSERT INTO customer_holding (id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status) VALUES ($1,$2,$3,1,100,101,1,1,'inactive')",(ident,uid,product)); execs.append((perf_counter()-t)*1000); local.append(ident)
   t=perf_counter(); await tx.commit(); commits.append((perf_counter()-t)*1000); ids.extend(local)
  except: await tx.rollback(); raise
  await conn.close()
 await asyncio.gather(*[worker() for _ in range(workers)])
 async with e.begin() as c: await c.exec_driver_sql("DELETE FROM customer_holding WHERE id = ANY($1::text[])",(ids,))
 await e.dispose(); return {'acquire':stats(acq),'execute':stats(execs),'commit':stats(commits),'writes':len(ids)}
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--workers',type=int,default=10); ap.add_argument('--rows',type=int,default=20); a=ap.parse_args(); s=get_settings(); result={'config':vars(a)}
 result['pool_5_10']=await scenario(s.database_url,5,10,a.workers,a.rows); result['pool_10_20']=await scenario(s.database_url,10,20,a.workers,a.rows); result['pass']=result['pool_5_10']['writes']==result['pool_10_20']['writes']==a.workers*a.rows; print(json.dumps(result,ensure_ascii=False,indent=2));
 if not result['pass']: raise SystemExit(1)
if __name__=='__main__': asyncio.run(main())
