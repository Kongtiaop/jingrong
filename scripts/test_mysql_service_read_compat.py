from __future__ import annotations

import asyncio
import json

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work

async def main():
    uow=create_mysql_validation_unit_of_work(get_settings().mysql_url)
    try:
        user=await uow.database.fetch_one("SELECT id FROM users WHERE username='customer_demo'")
        async with uow.transaction() as tx:
            customer=await uow.users.get(tx,user['id'])
            profile=await uow.profiles.get(tx,user['id'])
            risk=await uow.risks.get_active(tx,user['id'])
            account=await uow.accounts.get_by_user(tx,user['id'],for_update=True)
            product=await uow.database.fetch_one("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1")
            p=await uow.products.get_active(tx,product['id'])
        result={'customer':customer is not None,'profile':profile is not None,'risk':risk is not None,'account':account is not None,'product':p is not None,'attribute_access':bool(customer and customer.id and account.available_balance is not None and p.minimum_amount is not None)}
        result['pass']=all(result.values())
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        if not result['pass']: raise SystemExit(1)
    finally: await uow.close()
if __name__=='__main__': asyncio.run(main())
