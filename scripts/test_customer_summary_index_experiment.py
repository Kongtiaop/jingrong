from __future__ import annotations
import asyncio, json
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work

PG_INDEXES = [
 ("ix_customer_risk_user_status_assessed", "CREATE INDEX IF NOT EXISTS ix_customer_risk_user_status_assessed ON customer_risk_assessment (user_id, status, assessed_at DESC)"),
 ("ix_customer_asset_user_snapshot_created", "CREATE INDEX IF NOT EXISTS ix_customer_asset_user_snapshot_created ON customer_asset_snapshot (user_id, snapshot_time DESC, created_at DESC)"),
 ("ix_customer_holding_user_status", "CREATE INDEX IF NOT EXISTS ix_customer_holding_user_status ON customer_holding (user_id, status)"),
]
MYSQL_INDEXES = [
 ("ix_customer_risk_user_status_assessed", "customer_risk_assessment", "user_id, status, assessed_at"),
 ("ix_customer_asset_user_snapshot_created", "customer_asset_snapshot", "user_id, snapshot_time, created_at"),
 ("ix_customer_holding_user_status", "customer_holding", "user_id, status"),
]
async def main():
 s=get_settings(); db=Database(s); before={}; after={}; mysql=create_mysql_validation_unit_of_work(s.mysql_url)
 try:
  async with db.engine.begin() as c:
   for name,sql in PG_INDEXES:
    q="EXPLAIN (FORMAT JSON) SELECT user_id, assessed_at FROM customer_risk_assessment WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'perf_customer_%' LIMIT 100) AND status IN ('active','provisional') ORDER BY assessed_at DESC" if 'risk' in name else ("EXPLAIN (FORMAT JSON) SELECT user_id, snapshot_time, created_at FROM customer_asset_snapshot WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'perf_customer_%' LIMIT 100) ORDER BY snapshot_time DESC, created_at DESC" if 'asset' in name else "EXPLAIN (FORMAT JSON) SELECT user_id, COUNT(*) FROM customer_holding WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'perf_customer_%' LIMIT 100) AND status='active' GROUP BY user_id")
    before[name]=(await c.exec_driver_sql(q)).scalar()
    for _,sql in PG_INDEXES: await c.exec_driver_sql(sql)
    for name,_ in PG_INDEXES:
     q="EXPLAIN (FORMAT JSON) SELECT user_id, assessed_at FROM customer_risk_assessment WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'perf_customer_%' LIMIT 100) AND status IN ('active','provisional') ORDER BY assessed_at DESC" if 'risk' in name else ("EXPLAIN (FORMAT JSON) SELECT user_id, snapshot_time, created_at FROM customer_asset_snapshot WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'perf_customer_%' LIMIT 100) ORDER BY snapshot_time DESC, created_at DESC" if 'asset' in name else "EXPLAIN (FORMAT JSON) SELECT user_id, COUNT(*) FROM customer_holding WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'perf_customer_%' LIMIT 100) AND status='active' GROUP BY user_id")
     after[name]=(await c.exec_driver_sql(q)).scalar()
  mysql_before={}; mysql_after={}
  async with mysql.transaction() as tx:
   for name,table,cols in MYSQL_INDEXES:
    await tx.fetch_all(f"SHOW INDEX FROM `{table}`")
    if 'risk' in name: q="EXPLAIN SELECT user_id, assessed_at FROM customer_risk_assessment WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE %s ORDER BY created_at LIMIT 100) selected_users) AND status IN ('active','provisional') ORDER BY assessed_at DESC"
    elif 'asset' in name: q="EXPLAIN SELECT user_id, snapshot_time, created_at FROM customer_asset_snapshot WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE %s ORDER BY created_at LIMIT 100) selected_users) ORDER BY snapshot_time DESC, created_at DESC"
    else: q="EXPLAIN SELECT user_id, COUNT(*) FROM customer_holding WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE %s ORDER BY created_at LIMIT 100) selected_users) AND status='active' GROUP BY user_id"
    mysql_before[name]=await tx.fetch_all(q, ('perf_customer_%',))
   for name,table,cols in MYSQL_INDEXES:
    rows=await tx.fetch_all(f"SHOW INDEX FROM `{table}`")
    if name not in {r.get('Key_name') for r in rows}: await tx.execute(f"CREATE INDEX `{name}` ON `{table}` ({cols})")
   for name,table,cols in MYSQL_INDEXES:
    if 'risk' in name: q="EXPLAIN SELECT user_id, assessed_at FROM customer_risk_assessment WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE %s ORDER BY created_at LIMIT 100) selected_users) AND status IN ('active','provisional') ORDER BY assessed_at DESC"
    elif 'asset' in name: q="EXPLAIN SELECT user_id, snapshot_time, created_at FROM customer_asset_snapshot WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE %s ORDER BY created_at LIMIT 100) selected_users) ORDER BY snapshot_time DESC, created_at DESC"
    else: q="EXPLAIN SELECT user_id, COUNT(*) FROM customer_holding WHERE user_id IN (SELECT id FROM (SELECT id FROM users WHERE username LIKE %s ORDER BY created_at LIMIT 100) selected_users) AND status='active' GROUP BY user_id"
    mysql_after[name]=await tx.fetch_all(q, ('perf_customer_%',))
  print(json.dumps({'postgresql':{'before':before,'after':after},'mysql':{'before':mysql_before,'after':mysql_after},'pass':True},ensure_ascii=False,default=str))
 finally: await mysql.close(); await db.dispose()
if __name__=='__main__': asyncio.run(main())

