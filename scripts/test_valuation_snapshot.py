from __future__ import annotations
import asyncio, json
from decimal import Decimal
from sqlalchemy import func, select
from httpx import ASGITransport, AsyncClient
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, Product
from app.services.asset_summary_service import AssetSummaryService
from app.services.holding_valuation_service import HoldingValuationService, value_for
import jwt

async def main():
    db=Database(get_settings())
    try:
        async with db.session_factory() as s:
            user=(await s.execute(select(User).where(User.username=='hNW_demo'))).scalar_one()
            holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user.id,CustomerHolding.status=='active').order_by(CustomerHolding.created_at, CustomerHolding.id).limit(1))).scalar_one()
            product=(await s.execute(select(Product).where(Product.id==holding.product_id))).scalar_one()
            before=int((await s.execute(select(func.count()).select_from(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id))).scalar_one())
            await HoldingValuationService().revalue_user(s,user.id)
            snapshot=await AssetSummaryService().snapshot(s,user.id,source_type='valuation_test')
            await s.commit()
            expected_market, expected_profit, expected_rate=value_for(Decimal(str(holding.cost_amount)),Decimal(str(product.daily_yield_rate)),holding.holding_days)
            checks={
              'positive_daily_yield': Decimal(str(product.daily_yield_rate)) > 0,
              'product_name_configured': bool(product.name),
              'market_value_matches_rule': Decimal(str(holding.market_value)) == expected_market,
              'profit_matches_rule': Decimal(str(holding.profit_loss)) == expected_profit,
              'profit_positive': Decimal(str(holding.profit_loss)) > 0,
              'snapshot_written': int((await s.execute(select(func.count()).select_from(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id))).scalar_one()) == before + 1,
              'snapshot_matches_market_plus_cash': Decimal(str(snapshot.total_asset)) >= Decimal(str(snapshot.cash_balance)) + Decimal(str(holding.market_value)),
            }
            # Exercise the endpoint with a minimal app state backed by the same session factory.
            from fastapi import FastAPI
            from app.api.profile import router as profile_router
            test_app = FastAPI()
            test_app.include_router(profile_router)
            test_app.state.database = db
            settings = get_settings()
            token = jwt.encode({'sub': user.id, 'type': 'access'}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
            async with AsyncClient(transport=ASGITransport(app=test_app), base_url='http://test') as client:
                response = await client.get('/profile/me/assets/history?limit=60', headers={'Authorization': f'Bearer {token}'})
            history_payload = response.json().get('data', {}) if response.status_code == 200 else {}
            history_items = history_payload.get('items', [])
            checks['history_endpoint_ok'] = response.status_code == 200
            checks['history_includes_current_total'] = bool(history_items) and Decimal(str(history_items[-1]['total_asset'])) == Decimal(str(snapshot.total_asset))
            print(json.dumps({'checks':checks,'product':product.name,'daily_yield_rate':str(product.daily_yield_rate),'holding_days':holding.holding_days,'cost':str(holding.cost_amount),'market_value':str(holding.market_value),'profit_loss':str(holding.profit_loss),'total_asset':str(snapshot.total_asset),'history_total':str(history_items[-1]['total_asset']) if history_items else None,'pass':all(checks.values())},ensure_ascii=False,indent=2))
            raise SystemExit(0 if all(checks.values()) else 1)
    finally:
        await db.dispose()

if __name__=='__main__': asyncio.run(main())
