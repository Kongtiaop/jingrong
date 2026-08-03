from __future__ import annotations
import argparse, asyncio, json
from statistics import quantiles
from time import perf_counter
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_postgres_unit_of_work, create_mysql_validation_unit_of_work

def stats(values):
 values=sorted(values); p95=quantiles(values,n=20,method="inclusive")[18] if len(values)>=20 else values[-1]; p99=quantiles(values,n=100,method="inclusive")[98] if len(values)>=100 else values[-1]
 return {"p50_ms":round(values[len(values)//2],3),"p95_ms":round(p95,3),"p99_ms":round(p99,3),"min_ms":round(values[0],3),"max_ms":round(values[-1],3),"samples":len(values)}
async def run(call,warmups,samples):
 for _ in range(warmups): await call()
 vals=[]; rows=0
 for _ in range(samples):
  t=perf_counter(); result=await call(); vals.append((perf_counter()-t)*1000); rows=len(result)
 return {**stats(vals),"rows":rows}
async def concurrent(call,n):
 t=perf_counter(); results=await asyncio.gather(*[call() for _ in range(n)]); elapsed=(perf_counter()-t)*1000
 return {"requests":n,"elapsed_ms":round(elapsed,3),"rows":sorted({len(x) for x in results}),"avg_ms":round(elapsed/n,3)}
async def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--warmups',type=int,default=5); ap.add_argument('--samples',type=int,default=30); ap.add_argument('--concurrency',type=int,default=10); ap.add_argument('--limit',type=int,default=100); ap.add_argument('--offset',type=int,default=0); a=ap.parse_args()
 s=get_settings(); db=Database(s); pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(s.mysql_url)
 try:
  async with db.session_factory() as session:
   pg_call=lambda: pg.customer_queries.list_staff_customer_views(session,limit=a.limit,offset=a.offset)
   pg_seq=await run(pg_call,a.warmups,a.samples); pg_con=await concurrent(pg_call,a.concurrency)
  mysql_call=lambda: mysql.customer_queries.list_staff_customer_views(mysql.database,limit=a.limit,offset=a.offset)
  mysql_seq=await run(mysql_call,a.warmups,a.samples); mysql_con=await concurrent(mysql_call,a.concurrency)
  result={"scenario":"customer_summary_index_benchmark","config":vars(a),"postgresql":{"sequential":pg_seq,"concurrent":pg_con},"mysql":{"sequential":mysql_seq,"concurrent":mysql_con},"pass":pg_seq['rows']==a.limit and mysql_seq['rows']==a.limit and pg_con['rows']==[a.limit] and mysql_con['rows']==[a.limit]}
  print(json.dumps(result,ensure_ascii=False,indent=2))
  if not result['pass']: raise SystemExit(1)
 finally: await mysql.close(); await db.dispose()
if __name__=='__main__': asyncio.run(main())


