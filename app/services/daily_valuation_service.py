from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerAssetSnapshot
from app.services.asset_summary_service import AssetSummaryService
from app.services.holding_valuation_service import HoldingValuationService


class DailyValuationService:
    """Creates at most one deterministic daily valuation snapshot per customer/date."""

    def __init__(self, asset_summary_service: AssetSummaryService | None = None, holding_valuation_service: HoldingValuationService | None = None):
        self.assets = asset_summary_service or AssetSummaryService()
        self.holdings = holding_valuation_service or HoldingValuationService()

    async def run_for_user(self, session: AsyncSession, user_id: str, valuation_time: datetime | None = None) -> tuple[CustomerAssetSnapshot, bool]:
        valuation_time = valuation_time or datetime.now(timezone.utc)
        if valuation_time.tzinfo is None:
            valuation_time = valuation_time.replace(tzinfo=timezone.utc)
        valuation_day = valuation_time.date()
        existing = (await session.execute(select(CustomerAssetSnapshot).where(
            CustomerAssetSnapshot.user_id == user_id,
            CustomerAssetSnapshot.source_type == 'daily_valuation',
            CustomerAssetSnapshot.snapshot_time >= datetime.combine(valuation_day, datetime.min.time(), tzinfo=timezone.utc),
            CustomerAssetSnapshot.snapshot_time < datetime.combine(valuation_day, datetime.max.time(), tzinfo=timezone.utc),
        ).order_by(CustomerAssetSnapshot.snapshot_time.desc(), CustomerAssetSnapshot.id.desc()).limit(1))).scalars().first()
        if existing is not None:
            return existing, False
        await self.holdings.revalue_user(session, user_id, now=valuation_time)
        values = await self.assets.calculate(session, user_id, revalue=False)
        snapshot = CustomerAssetSnapshot(id=str(uuid4()), user_id=user_id, source_type='daily_valuation', snapshot_time=valuation_time, created_at=valuation_time, **values)
        await self.assets.assets.save(session, snapshot)
        return snapshot, True
