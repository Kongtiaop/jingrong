#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为压测灌入大量合成客户数据（users + customer_profile + customer_asset_snapshot + customer_risk_assessment）。

用法：
  conda run -n langchain_env python -m scripts.loadtest_seed_large --count 50000 --batch 1000

连接：读取 .env 的 DATABASE_URL 或 --dsn。默认 127.0.0.1:5433 wealth/wealth_dev/wealth_manager。
"""
import argparse
import asyncio
import os
import uuid

import asyncpg


def dsn_from_env_or_default(args) -> str:
    if args.dsn:
        return args.dsn
    dotenv = os.path.join(os.getcwd(), ".env")
    url = None
    if os.path.exists(dotenv):
        for line in open(dotenv, encoding="utf-8"):
            if line.strip().startswith("DATABASE_URL="):
                url = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                break
    return url or "postgresql://wealth:wealth_dev@127.0.0.1:5433/wealth_manager"


def pg_asyncpg(dsn: str) -> str:
    # asyncpg uses postgresql:// scheme; convert postgresql+asyncpg -> postgresql
    return dsn.replace("postgresql+asyncpg://", "postgresql://")


async def seed(conn: asyncpg.Connection, count: int, batch: int, start_i: int):
    users = []; profiles = []; assets = []; risks = []
    def new_rows(base_i):
        uid = str(uuid.uuid4())
        username = f"loadtest_c_{base_i}"
        users.append((uid, username, "DUMMYHASH", f"Loadtest {base_i}", "active", False))
        profiles.append((str(uuid.uuid4()), uid, 20 + (base_i % 50), "", None, "北京",
                         "individual", "ordinary", 3, "稳健增值", "3-5年", 4, "medium", "synthetic"))
        assets.append((str(uuid.uuid4()), uid, 100000 + base_i, 50000 + base_i, 40000 + base_i,
                       0, 90000 + base_i, "synthetic"))
        risks.append((str(uuid.uuid4()), uid, f"C{1 + base_i % 5}", 50, "{}", "active", "questionnaire"))
    inserted = 0
    start = start_i
    for b in range(start, count + start, batch):
        users.clear(); profiles.clear(); assets.clear(); risks.clear()
        for i in range(b, min(b + batch, count + start)):
            new_rows(i)
        async with conn.transaction():
            await conn.executemany(
                "INSERT INTO users(id,username,password_hash,display_name,status,is_super_admin) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT (username) DO NOTHING", users)
            await conn.executemany(
                "INSERT INTO customer_profile(id,user_id,age,occupation,annual_income,region,customer_type,customer_tier,investment_experience_years,investment_goal,investment_horizon,investment_horizon_years,liquidity_preference,source_type) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) ON CONFLICT (user_id) DO NOTHING", profiles)
            await conn.executemany(
                "INSERT INTO customer_asset_snapshot(id,user_id,total_asset,cash_balance,investable_asset,liability,net_asset,source_type) VALUES($1,$2,$3,$4,$5,$6,$7,$8)", assets)
            await conn.executemany(
                "INSERT INTO customer_risk_assessment(id,user_id,risk_level,score,answers_json,status,source_type) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING", risks)
        inserted += len(users)
        print(f"inserted {inserted}/{count + start}", flush=True)
    return inserted


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--count", type=int, default=50000, help="本次要插入的客户数")
    ap.add_argument("--batch", type=int, default=1000)
    a = ap.parse_args()
    dsn = pg_asyncpg(dsn_from_env_or_default(a))
    conn = await asyncpg.connect(dsn)
    try:
        # 已存在的 loadtest 前缀客户数，作为 offset，避免重复
        existing = await conn.fetchval("SELECT count(*) FROM users WHERE username LIKE 'loadtest\\_c\\_%'")
        print(f"已有 loadtest 客户: {existing}")
        start_i = existing
        n = await seed(conn, a.count, a.batch, start_i)
        total = await conn.fetchval("SELECT count(*) FROM users WHERE username LIKE 'loadtest\\_c\\_%'")
        print(f"完成: 本次 {n}, 累计 {total}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
