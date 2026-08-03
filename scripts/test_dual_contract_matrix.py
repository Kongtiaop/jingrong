from __future__ import annotations
import asyncio, json
from decimal import Decimal
from app.core.settings import get_settings
from app.repositories.unit_of_work import create_postgres_unit_of_work, create_mysql_validation_unit_of_work
from app.repositories.contracts import CustomerSummaryQueryRow

async def main():
 s=get_settings(); pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(s.mysql_url); result={"contracts":{}}
 try:
  user=await mysql.database.fetch_one("SELECT id FROM users WHERE username='customer_demo'")
  async with mysql.transaction() as tx:
   rows=await mysql.customer_queries.list_staff_customer_views(tx,limit=10,offset=0)
   result['contracts']['customer_summary_shape']=bool(rows) and all(isinstance(r,CustomerSummaryQueryRow) and set(r.normalized())=={'user_id','username','display_name','status','customer_type','customer_tier','risk_level','risk_score','holding_count','investable_asset'} for r in rows)
   result['contracts']['read_repositories']=all([await mysql.users.get(tx,user['id']), await mysql.profiles.get(tx,user['id']), await mysql.risks.get_active(tx,user['id']), await mysql.accounts.get_by_user(tx,user['id']), await mysql.assets.latest(tx,user['id'])])
  result['contracts']['uow_surfaces']=all(hasattr(pg,x) and hasattr(mysql,x) for x in ['users','profiles','risks','products','accounts','orders','holdings','assets','trades','status_history'])
  result['contracts']['mysql_shadow_only']=s.database_backend.lower()=='postgresql'
  result['pass']=all(result['contracts'].values()); print(json.dumps(result,ensure_ascii=False,indent=2,default=str));
  if not result['pass']: raise SystemExit(1)
 finally: await mysql.close()
if __name__=='__main__': asyncio.run(main())
