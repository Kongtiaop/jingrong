from __future__ import annotations
import asyncio, json
from decimal import Decimal
from sqlalchemy import func, select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, Product
from app.services.asset_summary_service import AssetSummaryService
from app.services.holding_valuation_service import HoldingValuationService, value_for

async def main():
    db=Database(get_settings())
    try:
        async with db.session_factory() as s:
            user=(await s.execute(select(User).where(User.username=='hNW_demo'))).scalar_one()
            holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user.id,CustomerHolding.status=='active'))).scalar_one()
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
              'snapshot_matches_market_plus_cash': Decimal(str(snapshot.total_asset)) == Decimal(str(snapshot.cash_balance)) + Decimal(str(holding.market_value)),
            }
            print(json.dumps({'checks':checks,'product':product.name,'daily_yield_rate':str(product.daily_yield_rate),'holding_days':holding.holding_days,'cost':str(holding.cost_amount),'market_value':str(holding.market_value),'profit_loss':str(holding.profit_loss),'total_asset':str(snapshot.total_asset),'pass':all(checks.values())},ensure_ascii=False,indent=2))
            raise SystemExit(0 if all(checks.values()) else 1)
    finally:
        await db.dispose()

if __name__=='__main__': asyncio.run(main())
