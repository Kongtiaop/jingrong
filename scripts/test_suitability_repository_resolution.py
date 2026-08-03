from __future__ import annotations
import asyncio,json
from app.core.settings import get_settings
from app.repositories.unit_of_work import create_postgres_unit_of_work,create_mysql_validation_unit_of_work
async def main():
 pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(get_settings().mysql_url)
 try:
  result={'postgres_products_list_active':hasattr(pg.products,'list_active'),'mysql_products_list_active':hasattr(mysql.products,'list_active'),'postgres_profiles_get':hasattr(pg.profiles,'get'),'mysql_profiles_get':hasattr(mysql.profiles,'get')}
  result['pass']=all(result.values()); print(json.dumps(result,ensure_ascii=False,indent=2));
  if not result['pass']: raise SystemExit(1)
 finally: await mysql.close()
if __name__=='__main__': asyncio.run(main())
