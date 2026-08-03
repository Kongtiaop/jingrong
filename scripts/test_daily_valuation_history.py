from __future__ import annotations
import asyncio, json
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import func, select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerHolding
from app.services.daily_valuation_service import DailyValuationService

async def main():
    db=Database(get_settings())
    try:
        async with db.session_factory() as s:
            user=(await s.execute(select(User).where(User.username=='asdasd'))).scalar_one()
            daily=(await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id,CustomerAssetSnapshot.source_type=='daily_valuation').order_by(CustomerAssetSnapshot.snapshot_time.desc()).limit(1))).scalars().first()
            if daily is None: raise RuntimeError('daily valuation snapshot missing')
            before={'id':daily.id,'total_asset':Decimal(str(daily.total_asset)),'source_type':daily.source_type,'snapshot_time':daily.snapshot_time}
            snapshot,created=await DailyValuationService().run_for_user(s,user.id,daily.snapshot_time)
            await s.commit()
            holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user.id,CustomerHolding.status=='active'))).scalar_one()
            history=list((await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id).order_by(CustomerAssetSnapshot.snapshot_time,CustomerAssetSnapshot.id))).scalars().all())
            same=(await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.id==before['id']))).scalar_one()
            checks={
              'same_day_idempotent':created is False and snapshot.id==before['id'],
              'daily_snapshot_immutable':same.source_type==before['source_type'] and Decimal(str(same.total_asset))==before['total_asset'],
              'positive_cross_day_profit':Decimal(str(holding.profit_loss))>0 and Decimal(str(holding.market_value))>Decimal(str(holding.cost_amount)),
              'history_has_trend_nodes':len(history)>=2 and Decimal(str(history[-1].total_asset))>Decimal(str(history[0].total_asset)),
            }
            print(json.dumps({'checks':checks,'daily_snapshot':{'id':same.id,'source_type':same.source_type,'total_asset':str(same.total_asset),'snapshot_time':same.snapshot_time.isoformat()},'holding':{'cost':str(holding.cost_amount),'market':str(holding.market_value),'profit':str(holding.profit_loss),'days':holding.holding_days},'history':[{'source':x.source_type,'total':str(x.total_asset),'time':x.snapshot_time.isoformat()} for x in history],'pass':all(checks.values())},ensure_ascii=False,indent=2))
            raise SystemExit(0 if all(checks.values()) else 1)
    finally: await db.dispose()
if __name__=='__main__': asyncio.run(main())
