from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work

async def main():
    uow=create_mysql_validation_unit_of_work(get_settings().mysql_url); user=await uow.database.fetch_one("SELECT id FROM users WHERE username='customer_demo'"); created_user=str(uuid4())
    account_id=None; asset_id=str(uuid4())
    try:
        async with uow.transaction() as tx:
            account=await uow.accounts.create_default(tx, created_user, 'AC-TEST-'+uuid4().hex[:8], Decimal('200000'))
            account_id=account.id
            asset=SimpleNamespace(id=asset_id,user_id=created_user,total_asset=Decimal('200000'),cash_balance=Decimal('200000'),investable_asset=Decimal('200000'),liability=Decimal('0'),net_asset=Decimal('200000'),source_type='compat_test')
            await uow.assets.save(tx, asset)
        result={'account_id':account_id,'asset_id':asset_id,'account_exists':account_id is not None,'asset_exists':(await uow.database.fetch_one('SELECT id FROM customer_asset_snapshot WHERE id=%s',(asset_id,))) is not None,'pass':account_id is not None}
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        if not result['pass']: raise SystemExit(1)
    finally:
        await uow.database.execute('DELETE FROM customer_asset_snapshot WHERE id=%s',(asset_id,));
        if account_id: await uow.database.execute('DELETE FROM account WHERE id=%s',(account_id,))
        await uow.close()
if __name__=='__main__': asyncio.run(main())
