from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work, create_postgres_unit_of_work

async def main():
    pg = create_postgres_unit_of_work()
    mysql = create_mysql_validation_unit_of_work(get_settings().mysql_url)
    order_id = str(uuid4())
    try:
        user = await mysql.database.fetch_one("SELECT id FROM users WHERE username='customer_demo'")
        operator = await mysql.database.fetch_one("SELECT id FROM users WHERE username='operations_demo'")
        account = await mysql.database.fetch_one("SELECT * FROM account WHERE user_id=%s", (user['id'],))
        product = await mysql.database.fetch_one("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1")
        async with mysql.transaction() as tx:
            await tx.insert_order({'id':order_id,'order_no':'UOW-'+order_id[:12],'user_id':user['id'],'account_id':account['id'],'product_id':product['id'],'amount':Decimal('1000.00'),'quantity':Decimal('1000.00'),'status':'pending_confirmation','side':'buy','review_note':'','failure_reason':'','idempotency_key':'uow-'+order_id})
            await tx.transition_order(order_id,'pending_confirmation','pending_review',user['id'],'uow contract')
            locked=await tx.lock_account(account['id'])
            await tx.update_account(account['id'],Decimal(str(locked['available_balance']))-Decimal('1000'),Decimal(str(locked['frozen_balance']))+Decimal('1000'))
            await tx.transition_order(order_id,'pending_review','executing',operator['id'],'uow contract')
            await tx.update_account(account['id'],Decimal(str(locked['available_balance']))-Decimal('1000'),Decimal(str(locked['frozen_balance'])))
            await tx.insert_trade({'id':str(uuid4()),'trade_no':'UOW-T-'+order_id[:10],'order_id':order_id,'user_id':user['id'],'product_id':product['id'],'amount':Decimal('1000'),'quantity':Decimal('1000')})
            await tx.transition_order(order_id,'executing','executed',operator['id'],'uow contract')
        row=await mysql.database.fetch_one("SELECT status FROM orders WHERE id=%s",(order_id,))
        result={'postgres_uow':type(pg).__name__,'mysql_uow':type(mysql).__name__,'mysql_order_status':row['status'] if row else None,'pass':row is not None and row['status']=='executed'}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if not result['pass']: raise SystemExit(1)
    finally:
        await mysql.database.execute("DELETE FROM order_status_history WHERE order_id=%s",(order_id,)); await mysql.database.execute("DELETE FROM trades WHERE order_id=%s",(order_id,)); await mysql.database.execute("DELETE FROM orders WHERE id=%s",(order_id,)); await mysql.close()
if __name__=='__main__': asyncio.run(main())
