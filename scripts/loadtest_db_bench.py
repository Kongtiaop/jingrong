#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio, time
import asyncpg

DSN = "postgresql://wealth:wealth_dev@127.0.0.1:5433/wealth_manager"

async def main():
    c = await asyncpg.connect(DSN)
    n = await c.fetchval("SELECT count(*) FROM users WHERE username LIKE 'loadtest\\_c\\_%'")
    print("loadtest customers:", n)
    # 代表性汇总查询：按层级统计（会扫 customer_profile）
    q = "SELECT customer_tier, count(*) FROM customer_profile GROUP BY customer_tier"
    lat = []
    for _ in range(20):
        t0 = time.perf_counter(); await c.fetch(q); lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    def p(x): return lat[min(len(lat) - 1, int(len(lat) * x))]
    print(f"GROUP BY tier @{n} rows: P50={p(.5):.2f}ms P95={p(.95):.2f}ms")
    # join/聚合
    q2 = "SELECT avg(total_asset), count(*) FROM customer_asset_snapshot"
    t0 = time.perf_counter(); await c.fetch(q2); print(f"avg(total_asset) agg: {(time.perf_counter()-t0)*1000:.2f}ms")
    # 某客户 PK 点查（indexed）
    q3 = "SELECT p.user_id FROM customer_profile p JOIN customer_asset_snapshot a ON a.user_id=p.user_id LIMIT 1"
    row = await c.fetchval(q3)
    if row:
        q4 = "SELECT p.user_id, a.total_asset FROM customer_profile p JOIN customer_asset_snapshot a ON a.user_id=p.user_id WHERE p.user_id=$1"
        lat2 = []
        for _ in range(20):
            t0 = time.perf_counter(); await c.fetch(q4, row); lat2.append((time.perf_counter()-t0)*1000)
        lat2.sort()
        print(f"PK join point query: P50={lat2[len(lat2)//2]:.2f}ms P95={lat2[-1]:.2f}ms")
    await c.close()

asyncio.run(main())
