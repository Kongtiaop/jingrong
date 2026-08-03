from __future__ import annotations
import asyncio, json, uuid
from decimal import Decimal
import httpx
from sqlalchemy import func, select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerHolding
from app.models.trading import Account, Order

BASE='http://127.0.0.1:8010/api/v1'
async def login(c,u,p):
    r=await c.post('/auth/login',json={'username':u,'password':p}); r.raise_for_status(); return {'Authorization':f"Bearer {r.json()['data']['access_token']}"}
async def main():
    db=Database(get_settings())
    try:
        async with db.session_factory() as s:
            user=(await s.execute(select(User).where(User.username=='asdasd'))).scalar_one()
            account=(await s.execute(select(Account).where(Account.user_id==user.id))).scalar_one()
            before_balance=Decimal(str(account.available_balance))
            before_snapshots=int((await s.execute(select(func.count()).select_from(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id))).scalar_one())
            user_id=user.id
        async with httpx.AsyncClient(base_url=BASE,timeout=30) as c:
            ch=await login(c,'asdasd','asdasdasdasd'); oh=await login(c,'operations_demo','Demo@2026Operations')
            products=(await c.get('/profile/products',headers=ch)).json()['data']
            product=next(p for p in products if p['name']=='成长精选组合')
            amount=Decimal('50000')
            created=await c.post('/trading/orders',headers=ch,json={'product_id':product['id'],'amount':str(amount),'idempotency_key':'asdasd-valuation-'+uuid.uuid4().hex})
            created.raise_for_status(); order_id=created.json()['data']['id']
            confirmed=await c.post(f'/trading/orders/{order_id}/confirm',headers=ch); confirmed.raise_for_status()
            reviewed=await c.post(f'/trading/orders/{order_id}/review',headers=oh,json={'note':'asdasd C4 valuation acceptance'}); reviewed.raise_for_status()
            summary=await c.get('/profile/me',headers=ch); summary.raise_for_status(); data=summary.json()['data']
        async with db.session_factory() as s:
            account=(await s.execute(select(Account).where(Account.user_id==user_id))).scalar_one()
            order=(await s.execute(select(Order).where(Order.id==order_id))).scalar_one()
            holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user_id,CustomerHolding.product_id==product['id'],CustomerHolding.status=='active'))).scalar_one()
            snapshots=int((await s.execute(select(func.count()).select_from(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user_id))).scalar_one())
            checks={
                'order_executed':order.status=='executed',
                'balance_debited':Decimal(str(account.available_balance))==before_balance-amount,
                'snapshot_written':snapshots>=before_snapshots+1,
                'summary_asset_present':data.get('latest_asset') is not None,
                'product_name_present':any(h.get('product_id')==product['id'] and h.get('product_name')=='成长精选组合' for h in data.get('holdings',[])),
                'daily_yield_present':any(h.get('product_id')==product['id'] and Decimal(str(h.get('daily_yield_rate',0)))==Decimal('0.00025') for h in data.get('holdings',[])),
                'holding_cost':Decimal(str(holding.cost_amount))>=amount,
                'asset_consistent':Decimal(str(data['latest_asset']['total_asset']))==Decimal(str(data['latest_asset']['cash_balance']))+sum((Decimal(str(h['market_value'])) for h in data['holdings']),Decimal('0')),
            }
            print(json.dumps({'checks':checks,'user':'asdasd','order_id':order_id,'product':'成长精选组合','risk_level':'C4','daily_yield_rate':'0.00025','available_before':str(before_balance),'available_after':str(account.available_balance),'holding':{'cost':str(holding.cost_amount),'market':str(holding.market_value),'profit':str(holding.profit_loss),'days':holding.holding_days},'latest_asset':data['latest_asset'],'pass':all(checks.values())},ensure_ascii=False,indent=2,default=str))
            raise SystemExit(0 if all(checks.values()) else 1)
    finally:
        await db.dispose()
if __name__=='__main__': asyncio.run(main())
