from __future__ import annotations

import asyncio
import json
from statistics import quantiles
from time import perf_counter

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import (
    create_mysql_validation_unit_of_work,
    create_postgres_unit_of_work,
)

SAMPLES = 7
WARMUPS = 2
MAX_P95_MS = 5000.0
MAX_DB_ROUND_TRIPS = 6


class QueryCounter:
    def __init__(self): self.count = 0
    def before_cursor_execute(self, *args): self.count += 1



async def measure(label, call, counter=None):
    for _ in range(WARMUPS):
        await call()
    if counter is not None:
        counter.count = 0
    values = []
    per_sample_counts = []
    for _ in range(SAMPLES):
        if counter is not None:
            counter.count = 0
        start = perf_counter()
        rows = await call()
        values.append((perf_counter() - start) * 1000)
        if counter is not None:
            per_sample_counts.append(counter.count)
    if counter is not None:
        counter.count = max(per_sample_counts, default=0)
    p95 = max(values) if len(values) < 20 else quantiles(values, n=20, method="inclusive")[18]
    return {
        "backend": label,
        "samples": [round(v, 3) for v in values],
        "p95_ms": round(p95, 3),
        "row_count": len(rows),
        "round_trips": max(per_sample_counts, default=0) if counter is not None else None,
        "max_round_trips": MAX_DB_ROUND_TRIPS,
        "pass": p95 <= MAX_P95_MS and len(rows) <= 100 and (counter is None or counter.count <= MAX_DB_ROUND_TRIPS),
    }


async def main():
    settings = get_settings()
    pg_uow = create_postgres_unit_of_work()
    mysql_uow = create_mysql_validation_unit_of_work(settings.mysql_url)
    pg_db = Database(settings)
    try:
        pg_counter = QueryCounter()
        event.listen(pg_db.engine.sync_engine, "before_cursor_execute", pg_counter.before_cursor_execute)
        async with pg_db.session_factory() as session:
            pg_result = await measure(
                "postgresql",
                lambda: pg_uow.customer_queries.list_staff_customer_views(session, limit=100, offset=0),
                pg_counter,
            )
        event.remove(pg_db.engine.sync_engine, "before_cursor_execute", pg_counter.before_cursor_execute)
        mysql_counter = QueryCounter()
        original_fetch_all = mysql_uow.database.fetch_all
        original_fetch_one = mysql_uow.database.fetch_one
        async def counted_all(sql, args=()):
            mysql_counter.count += 1
            return await original_fetch_all(sql, args)
        async def counted_one(sql, args=()):
            mysql_counter.count += 1
            return await original_fetch_one(sql, args)
        mysql_uow.database.fetch_all = counted_all
        mysql_uow.database.fetch_one = counted_one
        original_tx_all = __import__("app.repositories.mysql_profile", fromlist=["MySQLTransaction"]).MySQLTransaction.fetch_all
        original_tx_one = __import__("app.repositories.mysql_profile", fromlist=["MySQLTransaction"]).MySQLTransaction.fetch_one
        async def counted_tx_all(self, sql, args=()):
            mysql_counter.count += 1
            return await original_tx_all(self, sql, args)
        async def counted_tx_one(self, sql, args=()):
            mysql_counter.count += 1
            return await original_tx_one(self, sql, args)
        __import__("app.repositories.mysql_profile", fromlist=["MySQLTransaction"]).MySQLTransaction.fetch_all = counted_tx_all
        __import__("app.repositories.mysql_profile", fromlist=["MySQLTransaction"]).MySQLTransaction.fetch_one = counted_tx_one
        mysql_counter.count = 0
        async with mysql_uow.transaction() as tx:
            mysql_result = await measure(
                "mysql",
                lambda: mysql_uow.customer_queries.list_staff_customer_views(tx, limit=100, offset=0),
                mysql_counter,
            )
        result = {
            "scenario": "customer_summary_batch",
            "samples": SAMPLES,
            "warmups": WARMUPS,
            "thresholds": {"p95_ms": MAX_P95_MS, "max_round_trips": MAX_DB_ROUND_TRIPS},
            "postgresql": pg_result,
            "mysql": mysql_result,
            "pass": pg_result["pass"] and mysql_result["pass"],
            "note": "Round trips are counted at runtime: SQLAlchemy before_cursor_execute for PostgreSQL and adapter fetch calls for MySQL.",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        await mysql_uow.close()
        await pg_db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
