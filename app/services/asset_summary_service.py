from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerAssetSnapshot
from app.repositories.trading import SqlAlchemyAccountRepository, SqlAlchemyHoldingRepository, SqlAlchemyAssetRepository
from app.services.holding_valuation_service import HoldingValuationService


class AssetSummaryService:
    """Build one consistent, mock-safe wealth view from account + active holdings."""
    def __init__(self, account_repository=None, holding_repository=None, asset_repository=None):
        self.accounts=account_repository or SqlAlchemyAccountRepository(); self.holdings=holding_repository or SqlAlchemyHoldingRepository(); self.assets=asset_repository or SqlAlchemyAssetRepository(); self.valuations=HoldingValuationService()

    async def calculate(self, session: AsyncSession, user_id: str, *, revalue: bool = True) -> dict:
        if revalue:
            await self.valuations.revalue_user(session, user_id)
        account = await self.accounts.get_by_user(session, user_id)
        holdings_value = await self.holdings.sum_market_value(session, user_id)
        holdings_value = Decimal(str(holdings_value or 0))
        available = Decimal(str(account.available_balance if account else 0))
        frozen = Decimal(str(account.frozen_balance if account else 0))
        cash = available + frozen
        liability = Decimal("0")
        previous = await self.assets.latest(session, user_id)
        if previous is not None:
            liability = Decimal(str(previous.liability or 0))
        total = cash + holdings_value
        return {
            "total_asset": total,
            "cash_balance": cash,
            "investable_asset": total,
            "liability": liability,
            "net_asset": total - liability,
        }

    async def snapshot(self, session: AsyncSession, user_id: str, source_type: str = "derived") -> CustomerAssetSnapshot:
        values = await self.calculate(session, user_id, revalue=True)
        snapshot = CustomerAssetSnapshot(id=str(uuid4()), user_id=user_id, source_type=source_type, **values)
        return await self.assets.save(session, snapshot)

    async def latest_or_derived(self, session: AsyncSession, user_id: str) -> CustomerAssetSnapshot:
        now = datetime.now(timezone.utc)
        day_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
        day_end = datetime.combine(now.date(), datetime.max.time(), tzinfo=timezone.utc)
        # Historical event/daily snapshots are immutable. Only a same-day derived snapshot may be refreshed on read.
        latest = (await session.execute(
            __import__('sqlalchemy').select(CustomerAssetSnapshot).where(
                CustomerAssetSnapshot.user_id == user_id,
                CustomerAssetSnapshot.source_type == 'derived',
                CustomerAssetSnapshot.snapshot_time >= day_start,
                CustomerAssetSnapshot.snapshot_time < day_end,
            ).order_by(CustomerAssetSnapshot.snapshot_time.desc(), CustomerAssetSnapshot.id.desc()).limit(1)
        )).scalars().first()
        if latest is not None:
            # 复用当日派生快照，避免每次读都全量重估（account+holdings+assets 多次 DB 往返）
            return latest
        # 无当日快照才做完整计算
        values = await self.calculate(session, user_id, revalue=True)
        latest = CustomerAssetSnapshot(
            id=str(uuid4()),
            user_id=user_id,
            source_type='derived',
            snapshot_time=now,
            created_at=now,
            **values,
        )
        await self.assets.save(session, latest)
        return latest
