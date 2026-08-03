from __future__ import annotations

import asyncio
import json

from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work

QUERIES = {
    "users": ("SELECT u.id FROM users u WHERE u.status='active' ORDER BY u.created_at LIMIT 100 OFFSET 0", ()),
    "profiles": ("SELECT * FROM customer_profile WHERE user_id IN ({marks})", None),
    "risks": ("SELECT * FROM customer_risk_assessment WHERE user_id IN ({marks}) AND status IN ('active','provisional') ORDER BY assessed_at DESC", None),
    "holding_counts": ("SELECT user_id, COUNT(*) AS n FROM customer_holding WHERE user_id IN ({marks}) AND status='active' GROUP BY user_id", None),
    "assets": ("SELECT * FROM customer_asset_snapshot WHERE user_id IN ({marks}) ORDER BY snapshot_time DESC, created_at DESC", None),
}

async def main():
    settings = get_settings(); pg_db = Database(settings); mysql = create_mysql_validation_unit_of_work(settings.mysql_url); result = {}
    try:
        async with pg_db.engine.begin() as conn:
            users = await conn.exec_driver_sql("SELECT id FROM users WHERE status='active' ORDER BY created_at LIMIT 100 OFFSET 0")
            ids = [str(row[0]) for row in users.fetchall()]
            marks = ','.join(f"'{uid}'" for uid in ids)
            pg = {}
            for name, (sql, _) in QUERIES.items():
                sql = sql.replace('{marks}', marks)
                rows = await conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + sql)
                pg[name] = rows.scalar()
            result['postgresql'] = pg
        async with mysql.transaction() as tx:
            users = await tx.fetch_all("SELECT id FROM users WHERE status='active' ORDER BY created_at LIMIT %s OFFSET %s", (100, 0))
            ids = [row['id'] for row in users]
            marks = ','.join(['%s'] * len(ids))
            mysql_result = {}
            for name, (sql, args) in QUERIES.items():
                sql = sql.replace('{marks}', marks)
                params = args if args is not None else tuple(ids)
                mysql_result[name] = await tx.fetch_all("EXPLAIN " + sql, params)
            result['mysql'] = mysql_result
        def summarize(plan):
            if isinstance(plan, list) and plan and isinstance(plan[0], dict) and "Plan" in plan[0]:
                node = plan[0]["Plan"]
                return {"node_type": node.get("Node Type"), "index": node.get("Index Name"), "relation": node.get("Relation Name"), "plan_rows": node.get("Plan Rows"), "total_cost": node.get("Total Cost"), "extra": node.get("Plans", [{}])[0].get("Node Type") if node.get("Plans") else None}
            if isinstance(plan, list) and plan and isinstance(plan[0], dict):
                row = plan[0]
                return {"type": row.get("type"), "key": row.get("key"), "possible_keys": row.get("possible_keys"), "rows": row.get("rows"), "extra": row.get("Extra")}
            return {"kind": type(plan).__name__}
        result['summary'] = {"postgresql": {name: summarize(plan) for name, plan in result['postgresql'].items()}, "mysql": {name: summarize(plan) for name, plan in result['mysql'].items()}}
        result['query_count'] = len(QUERIES); result['pass'] = all(result.get(k) for k in ('postgresql','mysql'))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if not result['pass']: raise SystemExit(1)
    finally:
        await mysql.close(); await pg_db.dispose()

if __name__ == '__main__': asyncio.run(main())
