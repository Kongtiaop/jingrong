from __future__ import annotations
import asyncio,json
from app.core.settings import get_settings
from app.repositories.unit_of_work import create_postgres_unit_of_work,create_mysql_validation_unit_of_work
async def main():
 pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(get_settings().mysql_url)
 try:
  async with __import__('app.db.session',fromlist=['Database']).Database(get_settings()).session_factory() as s:
   rows=await pg.customer_queries.list_staff_customer_views(s,limit=10,offset=0)
  result={'postgres_rows':len(rows),'row_keys':sorted(rows[0].normalized().keys()) if rows else [],'mysql_query_attr':hasattr(mysql,'customer_queries'),'pass':bool(rows) and all(k in rows[0].normalized() for k in ('user_id','username','customer_type','risk_level','holding_count','investable_asset'))}
  print(json.dumps(result,ensure_ascii=False,indent=2,default=str));
  if not result['pass']: raise SystemExit(1)
 finally: await mysql.close()
if __name__=='__main__': asyncio.run(main())
