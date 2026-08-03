from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work

async def main():
    uow=create_mysql_validation_unit_of_work(get_settings().mysql_url); oid=str(uuid4()); hid=str(uuid4()); tid=str(uuid4()); sid=str(uuid4())
    try:
        user=await uow.database.fetch_one("SELECT id FROM users WHERE username='customer_demo'")
        account=await uow.database.fetch_one("SELECT * FROM account WHERE user_id=%s",(user['id'],))
        product=await uow.database.fetch_one("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1")
        async with uow.transaction() as tx:
            order=SimpleNamespace(id=oid,order_no='COMP-'+oid[:10],user_id=user['id'],account_id=account['id'],product_id=product['id'],amount=Decimal('1000'),quantity=Decimal('1000'),status='pending_confirmation',side='buy',review_note='',failure_reason='',idempotency_key='compat-'+oid)
            await uow.orders.save(tx,order)
            await uow.status_history.save(tx,SimpleNamespace(id=sid,order_id=oid,from_status=None,to_status='pending_confirmation',operator_user_id=user['id'],note='compat'))
            await uow.orders.update_status(tx,oid,'executed','compat','')
            holding=SimpleNamespace(id=hid,user_id=user['id'],product_id=product['id'],quantity=Decimal('1000'),cost_amount=Decimal('1000'),market_value=Decimal('1000'),profit_loss=Decimal('0'),holding_days=0,status='active')
            await uow.holdings.save(tx,holding)
            await uow.trades.save(tx,SimpleNamespace(id=tid,trade_no='COMP-T-'+oid[:8],order_id=oid,user_id=user['id'],product_id=product['id'],amount=Decimal('1000'),quantity=Decimal('1000')))
        row=await uow.database.fetch_one("SELECT status FROM orders WHERE id=%s",(oid,))
        result={'order_status':row['status'] if row else None,'pass':row is not None and row['status']=='executed'}
        print(json.dumps(result,ensure_ascii=False,indent=2));
        if not result['pass']: raise SystemExit(1)
    finally:
        await uow.database.execute("DELETE FROM order_status_history WHERE order_id=%s",(oid)); await uow.database.execute("DELETE FROM trades WHERE order_id=%s",(oid)); await uow.database.execute("DELETE FROM customer_holding WHERE id=%s",(hid)); await uow.database.execute("DELETE FROM orders WHERE id=%s",(oid)); await uow.close()
if __name__=='__main__': asyncio.run(main())
