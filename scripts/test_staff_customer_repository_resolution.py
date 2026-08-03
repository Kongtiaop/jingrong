from __future__ import annotations

import asyncio
import json

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_postgres_unit_of_work, create_mysql_validation_unit_of_work

async def main():
    pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(get_settings().mysql_url)
    try:
        result={
          'postgres_list_customers':hasattr(pg.profiles,'list_customers'),
          'postgres_holding_count_active':hasattr(pg.holdings,'count_active'),
          'mysql_list_customers':hasattr(mysql.profiles,'list_customers'),
          'mysql_holding_count_active':hasattr(mysql.holdings,'count_active'),
        }
        result['pass']=all(result.values()); print(json.dumps(result,ensure_ascii=False,indent=2));
        if not result['pass']: raise SystemExit(1)
    finally: await mysql.close()
if __name__=='__main__': asyncio.run(main())
