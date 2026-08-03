from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from uuid import uuid4

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work, create_postgres_unit_of_work

async def mysql_flow(uow):
    db=uow.database
    user=await db.fetch_one("SELECT id FROM users WHERE username='customer_demo'")
    op=await db.fetch_one("SELECT id FROM users WHERE username='operations_demo'")
    account=await db.fetch_one("SELECT * FROM account WHERE user_id=%s",(user['id'],))
    product=await db.fetch_one("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1")
    oid=str(uuid4()); amount=Decimal('1000')
    async with uow.transaction() as tx:
        await tx.insert_order({'id':oid,'order_no':'DUAL-'+oid[:12],'user_id':user['id'],'account_id':account['id'],'product_id':product['id'],'amount':amount,'quantity':amount,'status':'pending_confirmation','side':'buy','review_note':'','failure_reason':'','idempotency_key':'dual-'+oid})
        await tx.transition_order(oid,'pending_confirmation','pending_review',user['id'],'dual contract')
        locked=await tx.lock_account(account['id']); await tx.update_account(account['id'],Decimal(str(locked['available_balance']))-amount,Decimal(str(locked['frozen_balance']))+amount)
        await tx.transition_order(oid,'pending_review','executing',op['id'],'dual contract')
        await tx.update_account(account['id'],Decimal(str(locked['available_balance']))-amount,Decimal(str(locked['frozen_balance'])))
        await tx.insert_trade({'id':str(uuid4()),'trade_no':'DUAL-T-'+oid[:10],'order_id':oid,'user_id':user['id'],'product_id':product['id'],'amount':amount,'quantity':amount})
        await tx.transition_order(oid,'executing','executed',op['id'],'dual contract')
    row=await db.fetch_one("SELECT status,amount FROM orders WHERE id=%s",(oid,)); result={'status':row['status'],'amount':str(row['amount'])}
    await db.execute("DELETE FROM order_status_history WHERE order_id=%s",(oid)); await db.execute("DELETE FROM trades WHERE order_id=%s",(oid)); await db.execute("DELETE FROM orders WHERE id=%s",(oid)); return result

async def main():
    pg=create_postgres_unit_of_work(); mysql=create_mysql_validation_unit_of_work(get_settings().mysql_url)
    try:
        mysql_result=await mysql_flow(mysql)
        result={'postgres_uow':type(pg).__name__,'mysql_uow':type(mysql).__name__,'mysql_flow':mysql_result,'same_expected_status':mysql_result['status']=='executed','pass':mysql_result['status']=='executed'}
        print(json.dumps(result,ensure_ascii=False,indent=2));
        if not result['pass']: raise SystemExit(1)
    finally: await mysql.close()
if __name__=='__main__': asyncio.run(main())
