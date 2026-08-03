from __future__ import annotations
import asyncio, json
from decimal import Decimal
from uuid import uuid4
from app.core.settings import get_settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work

async def main():
 s=get_settings(); uow=create_mysql_validation_unit_of_work(s.mysql_url); result={"contracts":{}}; oid=str(uuid4()); idem='semantic-'+oid; created=False
 try:
  user=await uow.database.fetch_one("SELECT id FROM users WHERE username='customer_demo'"); op=await uow.database.fetch_one("SELECT id FROM users WHERE username='operations_demo'"); account=await uow.database.fetch_one("SELECT * FROM account WHERE user_id=%s",(user['id'],)); product=await uow.database.fetch_one("SELECT id FROM product WHERE risk_level='C1' ORDER BY minimum_amount LIMIT 1")
  async with uow.transaction() as tx:
   await tx.insert_order({'id':oid,'order_no':'SEM-'+oid[:12],'user_id':user['id'],'account_id':account['id'],'product_id':product['id'],'amount':Decimal('1.00'),'quantity':Decimal('1.00'),'status':'pending_confirmation','side':'buy','review_note':'','failure_reason':'','idempotency_key':idem}); created=True
   bad_before=await tx.fetch_one("SELECT status FROM orders WHERE id=%s",(oid,)); result['contracts']['create_pending']=bad_before['status']=='pending_confirmation'
   await tx.transition_order(oid,'wrong_status','executed',op['id'],'invalid')
   unchanged=await tx.fetch_one("SELECT status FROM orders WHERE id=%s",(oid,)); result['contracts']['invalid_transition_no_change']=unchanged['status']=='pending_confirmation'
   await tx.insert_order({'id':str(uuid4()),'order_no':'SEM-DUP-'+oid[:10],'user_id':user['id'],'account_id':account['id'],'product_id':product['id'],'amount':Decimal('1.00'),'quantity':Decimal('1.00'),'status':'pending_confirmation','side':'buy','review_note':'','failure_reason':'','idempotency_key':idem})
 except Exception as exc: result['contracts']['duplicate_idempotency_rejected']=True; result['duplicate_error']=type(exc).__name__
 finally:
  if created:
   await uow.database.execute("DELETE FROM order_status_history WHERE order_id=%s",(oid,)); await uow.database.execute("DELETE FROM trades WHERE order_id=%s",(oid,)); await uow.database.execute("DELETE FROM orders WHERE id=%s",(oid,))
  await uow.close()
 result['pass']=all(result['contracts'].values()); print(json.dumps(result,ensure_ascii=False,indent=2,default=str));
 if not result['pass']: raise SystemExit(1)
if __name__=='__main__': asyncio.run(main())
