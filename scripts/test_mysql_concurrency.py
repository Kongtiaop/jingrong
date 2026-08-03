from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import aiomysql

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")


async def connect(autocommit=False):
    return await aiomysql.connect(**MYSQL, autocommit=autocommit)


async def ids(conn):
    async with conn.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT id FROM users WHERE username='customer_demo' LIMIT 1")
        user = await cur.fetchone()
        await cur.execute("SELECT id FROM account WHERE user_id=%s LIMIT 1", (user['id'],))
        account = await cur.fetchone()
        await cur.execute("SELECT id FROM product ORDER BY name LIMIT 1")
        product = await cur.fetchone()
        return user['id'], account['id'], product['id']


async def concurrent_debit():
    setup = await connect(True)
    try:
        user_id, account_id, product_id = await ids(setup)
        async with setup.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT available_balance FROM account WHERE id=%s", (account_id,))
            before = Decimal(str((await cur.fetchone())['available_balance']))
            amount = Decimal('120000.00')
            # Keep the test isolated and restore the balance after both transactions.
            await cur.execute("UPDATE account SET available_balance=%s WHERE id=%s", (Decimal('100000.00'), account_id))
        async def worker(n):
            conn = await connect(False)
            try:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute("SET innodb_lock_wait_timeout=5")
                    await cur.execute("START TRANSACTION")
                    await cur.execute("SELECT available_balance FROM account WHERE id=%s FOR UPDATE", (account_id,))
                    current = Decimal(str((await cur.fetchone())['available_balance']))
                    if current < amount:
                        await conn.rollback()
                        return {'worker': n, 'result': 'insufficient', 'balance_seen': str(current)}
                    await cur.execute("UPDATE account SET available_balance=available_balance-%s WHERE id=%s", (amount, account_id))
                    await conn.commit()
                    return {'worker': n, 'result': 'debited', 'balance_seen': str(current)}
            finally:
                conn.close()
        results = await asyncio.gather(worker(1), worker(2))
        async with setup.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT available_balance FROM account WHERE id=%s", (account_id,))
            after_test = Decimal(str((await cur.fetchone())['available_balance']))
            await cur.execute("UPDATE account SET available_balance=%s WHERE id=%s", (before, account_id))
        debited = sum(1 for row in results if row['result'] == 'debited')
        return {'workers': results, 'debited_count': debited, 'balance_after_test': str(after_test), 'restored_balance': str(before), 'pass': debited == 0 or (debited == 1 and after_test == Decimal('0.00'))}
    finally:
        setup.close()


async def concurrent_idempotent_orders():
    setup = await connect(True)
    key = 'concurrent-' + uuid4().hex
    try:
        user_id, account_id, product_id = await ids(setup)
        async def worker(n):
            conn = await connect(False)
            order_id = str(uuid4())
            try:
                async with conn.cursor() as cur:
                    await cur.execute("START TRANSACTION")
                    sql = "INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending_confirmation','buy','','',%s)"
                    await cur.execute(sql, (order_id, f'CON-{n}-{order_id[:10]}', user_id, account_id, product_id, Decimal('1.00'), Decimal('1.0000'), key))
                    await conn.commit()
                    return {'worker': n, 'result': 'created', 'order_id': order_id}
            except Exception as exc:
                await conn.rollback()
                return {'worker': n, 'result': type(exc).__name__}
            finally:
                conn.close()
        results = await asyncio.gather(*[worker(i) for i in range(5)])
        async with setup.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id=%s AND idempotency_key=%s", (user_id, key))
            count = int((await cur.fetchone())['n'])
            await cur.execute("DELETE FROM orders WHERE user_id=%s AND idempotency_key=%s", (user_id, key))
        created = sum(1 for row in results if row['result'] == 'created')
        return {'results': results, 'created_count': created, 'rows_for_key': count, 'pass': created == 1 and count == 1}
    finally:
        setup.close()


async def order_state_and_trade_rollback():
    conn = await connect(False)
    try:
        user_id, account_id, product_id = await ids(conn)
        order_id, order_no = str(uuid4()), 'STATE-' + uuid4().hex[:10]
        trade_id, trade_no = str(uuid4()), 'TRADE-' + uuid4().hex[:10]
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("START TRANSACTION")
            await cur.execute("INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending_confirmation','buy','','',%s)", (order_id, order_no, user_id, account_id, product_id, Decimal('10.00'), Decimal('1.0000'), 'state-' + order_id))
            await cur.execute("UPDATE orders SET status='confirmed' WHERE id=%s", (order_id,))
            await cur.execute("INSERT INTO trades(id,trade_no,order_id,user_id,product_id,amount,quantity) VALUES(%s,%s,%s,%s,%s,%s,%s)", (trade_id, trade_no, order_id, user_id, product_id, Decimal('10.00'), Decimal('1.0000')))
            await cur.execute("UPDATE orders SET status='executed' WHERE id=%s", (order_id,))
            await conn.rollback()
            await cur.execute("SELECT COUNT(*) AS n FROM orders WHERE id=%s", (order_id,))
            orders = int((await cur.fetchone())['n'])
            await cur.execute("SELECT COUNT(*) AS n FROM trades WHERE id=%s", (trade_id,))
            trades = int((await cur.fetchone())['n'])
        return {'order_rows_after_rollback': orders, 'trade_rows_after_rollback': trades, 'pass': orders == 0 and trades == 0}
    finally:
        conn.close()


async def main():
    results = {
        'concurrent_debit': await concurrent_debit(),
        'concurrent_idempotent_orders': await concurrent_idempotent_orders(),
        'order_state_trade_rollback': await order_state_and_trade_rollback(),
    }
    results['all_pass'] = all(item['pass'] for item in results.values())
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    if not results['all_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
