import asyncio, asyncpg

async def main():
    c = await asyncpg.connect("postgresql://wealth:wealth_dev@127.0.0.1:5433/wealth_manager")
    rows = await c.fetch(
        "SELECT application_name, count(*) AS n FROM pg_stat_activity "
        "WHERE usename='wealth' AND pid <> pg_backend_pid() GROUP BY application_name")
    for r in rows:
        print(f"{r['application_name'] or '(none)'}: {r['n']}")
    tot = await c.fetchval(
        "SELECT count(*) FROM pg_stat_activity WHERE usename='wealth' AND pid <> pg_backend_pid()")
    print("total wealth conns:", tot)
    await c.close()

asyncio.run(main())
