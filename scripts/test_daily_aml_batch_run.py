from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_postgres_unit_of_work
from app.services.daily_aml_scan_service import DailyAmlScanService


async def main() -> None:
    db = Database(get_settings())
    batch_ids: list[str] = []
    try:
        at = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=12, minute=0, second=0, microsecond=0)
        service = DailyAmlScanService(create_postgres_unit_of_work())
        async with db.session_factory() as session:
            first, first_created = await service.run(session, as_of=at, days=7, username='customer_demo')
            await session.commit()
            batch_ids.append(first.id)
            assert first_created is True
            assert first.status == 'completed'
            assert first.total_customers == 1
            assert first.succeeded_customers == 1
            assert first.failed_customers == 0

        async with db.session_factory() as session:
            second, second_created = await service.run(session, as_of=at, days=7, username='customer_demo')
            await session.commit()
            assert second_created is False
            assert second.id == first.id

        async with db.session_factory() as session:
            audit_count = int((await session.execute(text("SELECT count(*) FROM audit_logs WHERE resource_type='aml_batch_run' AND resource_id=:id AND action='daily_aml_scan'"), {'id': first.id})).scalar_one())
            assert audit_count == 1
            print(json.dumps({
                'batch_id': first.id,
                'status': first.status,
                'first_created': first_created,
                'second_created': second_created,
                'total_customers': first.total_customers,
                'succeeded_customers': first.succeeded_customers,
                'failed_customers': first.failed_customers,
                'created_alerts': first.created_alerts,
                'audit_count': audit_count,
                'pass': True,
            }, ensure_ascii=False, indent=2))
    finally:
        async with db.engine.begin() as conn:
            if batch_ids:
                await conn.execute(text("DELETE FROM audit_logs WHERE resource_type='aml_batch_run' AND resource_id = ANY(:ids)"), {'ids': batch_ids})
                await conn.execute(text('DELETE FROM aml_batch_run WHERE id = ANY(:ids)'), {'ids': batch_ids})
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
