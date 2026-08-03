from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import aiomysql

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")


async def connect(autocommit=False):
    return await aiomysql.connect(**MYSQL, autocommit=autocommit)


async def get_demo_ids(conn):
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT id FROM users WHERE username='customer_demo' LIMIT 1")
        user = await cur.fetchone()
        await cur.execute("SELECT id FROM account WHERE user_id=%s LIMIT 1", (user["id"],))
        account = await cur.fetchone()
        await cur.execute("SELECT id FROM product ORDER BY name LIMIT 1")
        product = await cur.fetchone()
        return user["id"], account["id"], product["id"]


async def test_commit_rollback():
    conn = await connect(False)
    try:
        user_id, account_id, product_id = await get_demo_ids(conn)
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT available_balance FROM account WHERE id=%s", (account_id,))
            before = Decimal(str((await cur.fetchone())["available_balance"]))
            order_id = str(uuid4())
            await cur.execute("INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending_confirmation','buy','','',%s)", (order_id, "TX-" + order_id[:12], user_id, account_id, product_id, Decimal("12.34"), Decimal("1.0000"), "rollback-key"))
            await cur.execute("UPDATE account SET available_balance=available_balance-%s WHERE id=%s", (Decimal("12.34"), account_id))
            await conn.rollback()
            await cur.execute("SELECT available_balance FROM account WHERE id=%s", (account_id,))
            after = Decimal(str((await cur.fetchone())["available_balance"]))
            await cur.execute("SELECT COUNT(*) AS n FROM orders WHERE id=%s", (order_id,))
            order_count = (await cur.fetchone())["n"]
        return {"before": str(before), "after": str(after), "order_count_after_rollback": order_count, "pass": before == after and order_count == 0}
    finally:
        conn.close()


async def test_idempotency():
    conn = await connect(False)
    key = "tx-idem-" + uuid4().hex
    try:
        user_id, account_id, product_id = await get_demo_ids(conn)
        order_id = str(uuid4())
        async with conn.cursor(aiomysql.DictCursor) as cur:
            sql = "INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending_confirmation','buy','','',%s)"
            args = (order_id, "IDEM-" + order_id[:12], user_id, account_id, product_id, Decimal("1.00"), Decimal("1.0000"), key)
            await cur.execute(sql, args)
            await conn.commit()
            duplicate_error = None
            try:
                await cur.execute(sql, (str(uuid4()), "IDEM-2-" + order_id[:10], user_id, account_id, product_id, Decimal("1.00"), Decimal("1.0000"), key))
                await conn.commit()
            except Exception as exc:
                duplicate_error = type(exc).__name__
                await conn.rollback()
            await cur.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id=%s AND idempotency_key=%s", (user_id, key))
            count = (await cur.fetchone())["n"]
        return {"duplicate_error": duplicate_error, "rows_for_key": count, "pass": duplicate_error is not None and count == 1}
    finally:
        conn.close()


async def test_row_lock():
    first = await connect(False)
    second = await connect(False)
    try:
        _user_id, account_id, _product_id = await get_demo_ids(first)
        async with first.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("START TRANSACTION")
            await cur.execute("SELECT id FROM account WHERE id=%s FOR UPDATE", (account_id,))
            await cur.fetchone()
        async with second.cursor() as cur2:
            await cur2.execute("SET innodb_lock_wait_timeout=1")
            await cur2.execute("START TRANSACTION")
            blocked = False
            try:
                await cur2.execute("SELECT id FROM account WHERE id=%s FOR UPDATE", (account_id,))
                await cur2.fetchone()
            except Exception:
                blocked = True
                await second.rollback()
        await first.rollback()
        return {"second_reader_blocked": blocked, "pass": blocked}
    finally:
        first.close()
        second.close()


async def main():
    results = {
        "commit_rollback": await test_commit_rollback(),
        "idempotency": await test_idempotency(),
        "row_lock": await test_row_lock(),
    }
    results["all_pass"] = all(item["pass"] for item in results.values())
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    if not results["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
