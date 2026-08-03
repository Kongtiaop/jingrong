"""Reset the isolated MySQL migration database for repeatable development tests.

This script never touches PostgreSQL, Redis, or Milvus. It is intentionally
limited to the local migration database and requires an explicit confirmation
flag because it deletes all rows from migration tables.
"""
from __future__ import annotations

import argparse
import asyncio

import aiomysql

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")
CORE_TABLES = [
    "order_status_history", "trades", "orders", "account",
    "customer_holding", "customer_asset_snapshot",
    "customer_enterprise_verification", "customer_profile",
    "product_suitability_rule", "risk_rule", "product",
    "customer_risk_assessment", "users",
]
OPTIONAL_TABLES = ["audit_logs", "async_task", "outbox_event"]
PROBE_TABLES = ["migration_probe"]


async def table_names(cur):
    await cur.execute("SHOW TABLES")
    return {row[0] for row in await cur.fetchall()}


async def reset_mysql(include_probe: bool = True, include_auxiliary: bool = True):
    conn = await aiomysql.connect(**MYSQL, autocommit=False)
    try:
        async with conn.cursor() as cur:
            existing = await table_names(cur)
            tables = list(CORE_TABLES)
            if include_auxiliary:
                tables += OPTIONAL_TABLES
            if include_probe:
                tables += PROBE_TABLES
            selected = [table for table in tables if table in existing]
            if selected:
                await cur.execute("SET FOREIGN_KEY_CHECKS=0")
                for table in selected:
                    await cur.execute(f"TRUNCATE TABLE `{table}`")
                await cur.execute("SET FOREIGN_KEY_CHECKS=1")
            await conn.commit()
            return selected
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="confirm deletion of all selected MySQL migration rows")
    parser.add_argument("--keep-probe", action="store_true", help="keep migration_probe rows")
    parser.add_argument("--keep-auxiliary", action="store_true", help="keep audit/task/outbox rows")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to delete MySQL migration data without --yes")
    selected = await reset_mysql(include_probe=not args.keep_probe, include_auxiliary=not args.keep_auxiliary)
    print({"mysql_reset": True, "tables_truncated": selected, "probe_cleared": not args.keep_probe, "auxiliary_cleared": not args.keep_auxiliary})


if __name__ == "__main__":
    asyncio.run(main())
