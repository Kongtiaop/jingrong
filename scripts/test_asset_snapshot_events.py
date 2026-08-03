from __future__ import annotations
import asyncio, json, uuid
from decimal import Decimal
import httpx
from sqlalchemy import func, select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, Product
from app.models.trading import Account

BASE='http://127.0.0.1:8010/api/v1'

def count_query(user_id: str, source_type: str):
    return select(func.count()).select_from(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user_id,CustomerAssetSnapshot.source_type==source_type)

async def login(c,u,p):
    r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}

async def main():
    db=Database(get_settings())
    try:
        async with db.session_factory() as s:
            customer=(await s.execute(select(User).where(User.username=='customer_demo'))).scalar_one()
            target_user=(await s.execute(select(User).where(User.username=='customer_service_demo'))).scalar_one()
            source=(await s.execute(select(Account).where(Account.user_id==customer.id))).scalar_one()
            target=(await s.execute(select(Account).where(Account.user_id==target_user.id))).scalar_one()
            product=(await s.execute(select(Product).where(Product.status=='active',Product.target_customer_type=='all'))).scalars().first()
            holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==customer.id,CustomerHolding.status=='active'))).scalars().first()
            before={name:int((await s.execute(count_query(uid,source_type))).scalar_one()) for name,uid,source_type in [('trade',customer.id,'trade_derived'),('redeem',customer.id,'redeem_settlement'),('transfer_out',customer.id,'transfer_settlement'),('transfer_in',target_user.id,'transfer_settlement')]}
            ids={'customer':customer.id,'target':target_user.id,'source_account':source.id,'target_account':target.id,'target_no':target.account_no,'product_id':product.id,'holding_product_id':holding.product_id}
        async with httpx.AsyncClient(base_url=BASE,timeout=30) as c:
            ch=await login(c,'customer_demo','Demo@2026Customer'); oh=await login(c,'operations_demo','Demo@2026Operations')
            # Order executes and must produce trade_derived snapshot.
            order=await c.post('/trading/orders',headers=ch,json={'product_id':ids['product_id'],'amount':'1000','idempotency_key':'asset-snapshot-order-'+uuid.uuid4().hex}); order.raise_for_status(); oid=order.json()['data']['id']
            (await c.post(f'/trading/orders/{oid}/confirm',headers=ch)).raise_for_status(); review=await c.post(f'/trading/orders/{oid}/review',headers=oh,json={'note':'asset snapshot order'}); review.raise_for_status()
            # Redeem settlement must produce redeem snapshot.
            redeem=await c.post('/operations/redeem',headers=ch,json={'product_id':ids['holding_product_id'],'quantity':'1','amount':'1','idempotency_key':'asset-snapshot-redeem-'+uuid.uuid4().hex}); redeem.raise_for_status(); rid=redeem.json()['data']['operation_id']; rr=await c.post(f'/operations/redeem/{rid}/review',headers=oh,json={'approve':True,'note':'asset snapshot redeem'}); rr.raise_for_status()
            # Transfer settlement must snapshot source and target.
            transfer=await c.post('/operations/transfer',headers=ch,json={'to_account':ids['target_no'],'amount':'2','idempotency_key':'asset-snapshot-transfer-'+uuid.uuid4().hex}); transfer.raise_for_status(); tid=transfer.json()['data']['operation_id']; tr=await c.post(f'/operations/transfer/{tid}/review',headers=oh,json={'approve':True,'note':'asset snapshot transfer'}); tr.raise_for_status()
        async with db.session_factory() as s:
            after={name:int((await s.execute(count_query(uid,source_type))).scalar_one()) for name,uid,source_type in [('trade',ids['customer'],'trade_derived'),('redeem',ids['customer'],'redeem_settlement'),('transfer_out',ids['customer'],'transfer_settlement'),('transfer_in',ids['target'],'transfer_settlement')]}
            latest=(await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==ids['customer']).order_by(CustomerAssetSnapshot.snapshot_time.desc(),CustomerAssetSnapshot.id.desc()).limit(1))).scalars().first()
            account=(await s.execute(select(Account).where(Account.user_id==ids['customer']))).scalar_one()
            holdings=list((await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==ids['customer'],CustomerHolding.status=='active'))).scalars().all())
            expected=Decimal(str(account.available_balance))+Decimal(str(account.frozen_balance))+sum((Decimal(str(h.market_value)) for h in holdings),Decimal('0'))
            checks={'order_snapshot':after['trade']==before['trade']+1,'redeem_snapshot':after['redeem']==before['redeem']+1,'transfer_source_snapshot':after['transfer_out']==before['transfer_out']+1,'transfer_target_snapshot':after['transfer_in']==before['transfer_in']+1,'latest_snapshot_consistent':Decimal(str(latest.total_asset))==expected}
            print(json.dumps({'checks':checks,'before':before,'after':after,'latest_source_type':latest.source_type,'latest_total_asset':str(latest.total_asset),'expected_total_asset':str(expected),'pass':all(checks.values())},ensure_ascii=False,indent=2)); raise SystemExit(0 if all(checks.values()) else 1)
    finally:
        await db.dispose()

if __name__=='__main__': asyncio.run(main())
