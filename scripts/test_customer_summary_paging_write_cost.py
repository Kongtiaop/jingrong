from __future__ import annotations
import argparse, asyncio, json
from statistics import quantiles
from time import perf_counter
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_postgres_unit_of_work, create_mysql_validation_unit_of_work

def stats(vals):
 vals=sorted(vals); return {"p50_ms":round(vals[len(vals)//2],3),"p95_ms":round(vals[-1] if len(vals)<20 else quantiles(vals,n=20,method="inclusive")[18],3),"max_ms":round(vals[-1],3),"samples":len(vals)}
async def read_bench(call, warmups, samples):
 for _ in range(warmups): await call()
 vals=[]; rows=0
 for _ in range(samples):
  t=perf_counter(); result=await call(); vals.append((perf_counter()-t)*1000); rows=len(result)
 return {**stats(vals),"rows":rows}
async def write_bench(db, backend, n):
 times=[]
 if backend=='postgresql':
  async with db.engine.begin() as c:
   for i in range(n):
    t=perf_counter(); await c.exec_driver_sql("CREATE TEMP TABLE IF NOT EXISTS perf_write_probe (id INTEGER)"); await c.exec_driver_sql("INSERT INTO perf_write_probe (id) VALUES ($1)",(i,)); times.append((perf_counter()-t)*1000)
 else:
  async with db.transaction() as tx:
   await tx.execute("CREATE TEMPORARY TABLE IF NOT EXISTS perf_write_probe (id INT)")
   for i in range(n):
    t=perf_counter(); await tx.execute("INSERT INTO perf_write_probe (id) VALUES (%s)",(i,)); times.append((perf_counter()-t)*1000)
 return {**stats(times),"writes":n}
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--warmups',type=int,default=5); ap.add_argument('--samples',type=int,default=15); ap.add_argument('--write-samples',type=int,default=20); a=ap.parse_args(); offsets=[0,100,500,1000,2500,4900]
 s=get_settings(); pgdb=Database(s); pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(s.mysql_url); result={"config":vars(a),"offsets":offsets}
 try:
  async with pgdb.session_factory() as session:
   result['postgresql']={str(o):await read_bench(lambda o=o: pg.customer_queries.list_staff_customer_views(session,limit=100,offset=o),a.warmups,a.samples) for o in offsets}
  result['postgresql']['write_cost']=await write_bench(pgdb,'postgresql',a.write_samples)
  result['mysql']={}
  for o in offsets:
   result['mysql'][str(o)]=await read_bench(lambda o=o: mysql.customer_queries.list_staff_customer_views(mysql.database,limit=100,offset=o),a.warmups,a.samples)
  result['mysql']['write_cost']=await write_bench(mysql,'mysql',a.write_samples)
  result['pass']=all(result[b][str(o)]['rows']==(100 if o<5000 else 0) for b in ('postgresql','mysql') for o in offsets)
  print(json.dumps(result,ensure_ascii=False,indent=2));
  if not result['pass']: raise SystemExit(1)
 finally: await mysql.close(); await pgdb.dispose()
if __name__=='__main__': asyncio.run(main())

