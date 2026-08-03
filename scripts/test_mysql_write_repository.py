from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

from app.repositories.mysql_profile import MySQLRepository, MySQLTransaction

MYSQL_URL = "mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3307/wealth_manager"

async def main():
    db = MySQLRepository(MYSQL_URL)
    order_id = str(uuid4())
    idem = "mysql-write-" + uuid4().hex
    try:
        user = await db.fetch_one("SELECT id FROM users WHERE username='customer_demo'")
        operator = await db.fetch_one("SELECT id FROM users WHERE username='operations_demo'")
        account = await db.fetch_one("SELECT * FROM account WHERE user_id=%s", (user['id'],))
        product = await db.fetch_one("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1")
        amount = Decimal('1000.00')
        async with MySQLTransaction(db) as tx:
            await tx.insert_order({'id':order_id,'order_no':'MW-'+order_id[:12],'user_id':user['id'],'account_id':account['id'],'product_id':product['id'],'amount':amount,'quantity':amount,'status':'pending_confirmation','side':'buy','review_note':'','failure_reason':'','idempotency_key':idem})
            await tx.transition_order(order_id, 'pending_confirmation', 'pending_review', user['id'], 'mysql write repository')
            locked = await tx.lock_account(account['id'])
            await tx.update_account(account['id'], Decimal(str(locked['available_balance']))-amount, Decimal(str(locked['frozen_balance']))+amount)
            await tx.transition_order(order_id, 'pending_review', 'executing', operator['id'], 'mysql write repository')
            await tx.update_account(account['id'], Decimal(str(locked['available_balance']))-amount, Decimal(str(locked['frozen_balance'])))
            await tx.insert_trade({'id':str(uuid4()),'trade_no':'MT-'+order_id[:12],'order_id':order_id,'user_id':user['id'],'product_id':product['id'],'amount':amount,'quantity':amount})
            await tx.transition_order(order_id, 'executing', 'executed', operator['id'], 'mysql write repository')
        order = await db.fetch_one("SELECT status,amount FROM orders WHERE id=%s", (order_id,))
        trade = await db.fetch_one("SELECT order_id FROM trades WHERE order_id=%s", (order_id,))
        result={'order':order,'trade':trade,'pass':order and order['status']=='executed' and trade is not None}
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        if not result['pass']: raise SystemExit(1)
    finally:
        await db.execute("DELETE FROM order_status_history WHERE order_id=%s", (order_id,))
        await db.execute("DELETE FROM trades WHERE order_id=%s", (order_id,))
        await db.execute("DELETE FROM orders WHERE id=%s", (order_id,))
        await db.close()

if __name__=='__main__': asyncio.run(main())
