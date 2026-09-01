#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理 loadtest 灌入的合成客户数据（user 前缀 loadtest_c_）。

按外键依赖顺序删除：asset -> risk -> profile -> users。
用法：conda run -n langchain_env python -m scripts.loadtest_cleanup
"""
import asyncio
import os
import asyncpg

DSN = os.environ.get(
    "LOADTEST_DSN", "postgresql://wealth:wealth_dev@127.0.0.1:5433/wealth_manager"
)


async def main():
    c = await asyncpg.connect(DSN)
    like = "loadtest\\_c\\_%"
    n = await c.fetchval("SELECT count(*) FROM users WHERE username LIKE $1", like)
    print(f"待清理 loadtest 客户: {n}")
    if n and n > 0:
        async with c.transaction():
            for tbl in ("customer_asset_snapshot", "customer_risk_assessment", "customer_profile"):
                d = await c.execute(
                    f"DELETE FROM {tbl} WHERE user_id IN (SELECT id FROM users WHERE username LIKE $1)", like)
                print(f"{tbl}: {d}")
            d = await c.execute("DELETE FROM users WHERE username LIKE $1", like)
            print(f"users: {d}")
    left = await c.fetchval("SELECT count(*) FROM users WHERE username LIKE $1", like)
    print(f"剩余 loadtest 客户: {left}")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
