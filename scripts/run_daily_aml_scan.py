from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.unit_of_work import create_postgres_unit_of_work
from app.services.daily_aml_scan_service import DailyAmlScanService


def parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def main() -> None:
    parser = argparse.ArgumentParser(description='Run daily AML customer-window scan.')
    parser.add_argument('--at', default='', help='ISO timestamp used to derive the UTC scan window')
    parser.add_argument('--days', type=int, default=7, choices=range(1, 31))
    parser.add_argument('--username', default='', help='Optional single customer username')
    parser.add_argument('--repeat', type=int, default=1, choices=range(1, 101), help='Run the same idempotent scan repeatedly for smoke verification')
    args = parser.parse_args()
    db = Database(get_settings())
    try:
        service = DailyAmlScanService(create_postgres_unit_of_work())
        as_of = parse_time(args.at)
        runs = []
        for index in range(args.repeat):
            async with db.session_factory() as session:
                run, created = await service.run(
                    session, as_of=as_of, days=args.days, username=args.username,
                )
                await session.commit()
                runs.append({
                    'run_no': index + 1,
                    'created': created,
                    'batch_id': run.id,
                    'batch_key': run.batch_key,
                    'status': run.status,
                    'scan_window_start': run.scan_window_start.isoformat(),
                    'scan_window_end': run.scan_window_end.isoformat(),
                    'total_customers': run.total_customers,
                    'succeeded_customers': run.succeeded_customers,
                    'failed_customers': run.failed_customers,
                    'created_alerts': run.created_alerts,
                    'error_summary': run.error_summary,
                })
        print(json.dumps({
            'as_of': as_of.isoformat(),
            'days': args.days,
            'username': args.username or None,
            'repeat': args.repeat,
            'runs': runs,
        }, ensure_ascii=False, indent=2))
    finally:
        await db.dispose()


if __name__ == '__main__':
    asyncio.run(main())
