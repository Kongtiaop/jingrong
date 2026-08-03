from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

import aiomysql

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")


async def conn(autocommit=False):
    return await aiomysql.connect(**MYSQL, autocommit=autocommit)


async def ids(c):
    async with c.cursor(aiomysql.DictCursor) as cur:
        await cur.execute("SELECT id FROM users WHERE username='customer_demo' LIMIT 1")
        user = await cur.fetchone()
        await cur.execute("SELECT id FROM users WHERE username='operations_demo' LIMIT 1")
        operator = await cur.fetchone()
        await cur.execute("SELECT id FROM account WHERE user_id=%s LIMIT 1", (user['id'],))
        account = await cur.fetchone()
        await cur.execute("SELECT id, minimum_amount, risk_level, target_customer_type FROM product WHERE status='active' ORDER BY minimum_amount LIMIT 1")
        product = await cur.fetchone()
        return user, operator, account, product


async def flow():
    c = await conn(False)
    try:
        user, operator, account, product = await ids(c)
        order_id = str(uuid4())
        order_no = 'SVC-' + uuid4().hex[:12]
        idem = 'svc-' + uuid4().hex
        amount = max(Decimal(str(product['minimum_amount'] or 0)), Decimal('100.00'))
        async with c.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("START TRANSACTION")
            await cur.execute("SELECT available_balance, frozen_balance FROM account WHERE id=%s FOR UPDATE", (account['id'],))
            initial = await cur.fetchone()
            if Decimal(str(initial['available_balance'])) < amount:
                raise AssertionError('test account lacks enough balance')
            await cur.execute("INSERT INTO orders(id,order_no,user_id,account_id,product_id,amount,quantity,status,side,review_note,failure_reason,idempotency_key) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending_confirmation','buy','','',%s)", (order_id, order_no, user['id'], account['id'], product['id'], amount, amount, idem))
            await cur.execute("INSERT INTO order_status_history(id,order_id,from_status,to_status,operator_user_id,note) VALUES(%s,%s,%s,%s,%s,%s)", (str(uuid4()), order_id, None, 'pending_confirmation', user['id'], 'customer created order'))
            await cur.execute("SELECT id FROM orders WHERE user_id=%s AND idempotency_key=%s", (user['id'], idem))
            duplicate_read = await cur.fetchone()
            if not duplicate_read or duplicate_read['id'] != order_id:
                raise AssertionError('idempotency read did not return created order')
            await cur.execute("UPDATE account SET available_balance=available_balance-%s, frozen_balance=frozen_balance+%s WHERE id=%s", (amount, amount, account['id']))
            await cur.execute("UPDATE orders SET status='pending_review', updated_at=NOW() WHERE id=%s AND status='pending_confirmation'", (order_id,))
            await cur.execute("INSERT INTO order_status_history(id,order_id,from_status,to_status,operator_user_id,note) VALUES(%s,%s,%s,%s,%s,%s)", (str(uuid4()), order_id, 'pending_confirmation', 'pending_review', user['id'], 'customer confirmed order'))
            await cur.execute("UPDATE orders SET status='executing', review_note=%s, updated_at=NOW() WHERE id=%s AND status='pending_review'", ('approved by staff', order_id))
            await cur.execute("UPDATE account SET frozen_balance=frozen_balance-%s WHERE id=%s", (amount, account['id']))
            await cur.execute("INSERT INTO customer_holding(id,user_id,product_id,quantity,cost_amount,market_value,profit_loss,holding_days,status) VALUES(%s,%s,%s,%s,%s,%s,0,0,'active') ON DUPLICATE KEY UPDATE quantity=quantity+VALUES(quantity), cost_amount=cost_amount+VALUES(cost_amount), market_value=market_value+VALUES(market_value)", (str(uuid4()), user['id'], product['id'], amount, amount, amount))
            # Check if the schema has a unique holding key; if not, this insert remains a valid isolated flow.
            await cur.execute("INSERT INTO trades(id,trade_no,order_id,user_id,product_id,amount,quantity) VALUES(%s,%s,%s,%s,%s,%s,%s)", (str(uuid4()), 'SVC-T-' + uuid4().hex[:10], order_id, user['id'], product['id'], amount, amount))
            await cur.execute("UPDATE orders SET status='executed', updated_at=NOW() WHERE id=%s AND status='executing'", (order_id,))
            await cur.execute("INSERT INTO order_status_history(id,order_id,from_status,to_status,operator_user_id,note) VALUES(%s,%s,%s,%s,%s,%s)", (str(uuid4()), order_id, 'executing', 'executed', operator['id'], 'mock trade executed'))
            await c.commit()
            await cur.execute("SELECT status, amount FROM orders WHERE id=%s", (order_id,))
            final_order = await cur.fetchone()
            await cur.execute("SELECT available_balance, frozen_balance FROM account WHERE id=%s", (account['id'],))
            final_account = await cur.fetchone()
            await cur.execute("SELECT COUNT(*) AS n FROM trades WHERE order_id=%s", (order_id,))
            trade_count = int((await cur.fetchone())['n'])
            await cur.execute("SELECT COUNT(*) AS n FROM order_status_history WHERE order_id=%s", (order_id,))
            history_count = int((await cur.fetchone())['n'])
            await cur.execute("DELETE FROM order_status_history WHERE order_id=%s", (order_id,))
            await cur.execute("DELETE FROM trades WHERE order_id=%s", (order_id,))
            await cur.execute("DELETE FROM orders WHERE id=%s", (order_id,))
            await cur.execute("DELETE FROM customer_holding WHERE user_id=%s AND product_id=%s AND quantity=%s AND cost_amount=%s", (user['id'], product['id'], amount, amount))
            await cur.execute("UPDATE account SET available_balance=%s, frozen_balance=%s WHERE id=%s", (initial['available_balance'], initial['frozen_balance'], account['id']))
            await c.commit()
        return {'status': final_order['status'], 'amount': str(final_order['amount']), 'trade_count': trade_count, 'history_count': history_count, 'available_after': str(final_account['available_balance']), 'frozen_after': str(final_account['frozen_balance']), 'pass': final_order['status'] == 'executed' and trade_count == 1 and history_count == 3 and Decimal(str(final_account['frozen_balance'])) == Decimal(str(initial['frozen_balance']))}
    finally:
        c.close()


async def main():
    result = await flow()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result['pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
