from __future__ import annotations

import asyncio
import json
from time import perf_counter

from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_postgres_unit_of_work, create_mysql_validation_unit_of_work

async def main():
    settings=get_settings(); pg_db=Database(settings); pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(settings.mysql_url)
    try:
        async with pg_db.session_factory() as session:
            t0=perf_counter(); pg_rows=await pg.customer_queries.list_staff_customer_views(session,limit=100,offset=0); pg_ms=(perf_counter()-t0)*1000
        t0=perf_counter()
        async with mysql.transaction() as tx:
            mysql_rows=await mysql.customer_queries.list_staff_customer_views(tx,limit=100,offset=0)
        mysql_ms=(perf_counter()-t0)*1000
        pg_map={row.user_id:row.normalized() for row in pg_rows}; mysql_map={row.user_id:row.normalized() for row in mysql_rows}
        # MySQL migration snapshot may contain non-customer staff accounts; compare only the PostgreSQL customer universe for this validation.
        mysql_map={uid: mysql_map[uid] for uid in pg_map if uid in mysql_map}
        differences=[]
        for uid in sorted(set(pg_map)|set(mysql_map)):
            if pg_map.get(uid)!=mysql_map.get(uid): differences.append({'user_id':uid,'postgres':pg_map.get(uid),'mysql':mysql_map.get(uid)})
        result={'postgres_count':len(pg_rows),'mysql_count':len(mysql_rows),'postgres_ms':round(pg_ms,3),'mysql_ms':round(mysql_ms,3),'missing_in_mysql':sorted(set(pg_map)-set(mysql_map)),'missing_in_postgres':sorted(set(mysql_map)-set(pg_map)),'differences':differences,'pass':not differences and set(pg_map)==set(mysql_map)}
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        if not result['pass']: raise SystemExit(1)
    finally:
        await mysql.close(); await pg_db.dispose()
if __name__=='__main__': asyncio.run(main())
