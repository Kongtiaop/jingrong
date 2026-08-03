from __future__ import annotations
import argparse, asyncio, json
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.services.daily_valuation_service import DailyValuationService

def parse_time(value: str | None):
    if not value: return datetime.now(timezone.utc)
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

async def main():
    parser=argparse.ArgumentParser(description='Run deterministic demo daily valuation.')
    parser.add_argument('--username', default='')
    parser.add_argument('--at', default='', help='ISO timestamp, e.g. 2026-08-04T09:00:00+08:00')
    args=parser.parse_args(); at=parse_time(args.at); db=Database(get_settings())
    try:
        async with db.session_factory() as session:
            query=select(User).where(User.roles.any(code='customer'))
            if args.username: query=query.where(User.username==args.username)
            users=list((await session.execute(query)).scalars().all())
            if not users: raise RuntimeError('no matching customer user')
            service=DailyValuationService(); rows=[]
            for user in users:
                snapshot, created=await service.run_for_user(session,user.id,at)
                rows.append({'username':user.username,'created':created,'snapshot_id':snapshot.id,'snapshot_time':snapshot.snapshot_time.isoformat(),'total_asset':str(snapshot.total_asset),'source_type':snapshot.source_type})
            await session.commit(); print(json.dumps({'valuation_time':at.isoformat(),'items':rows},ensure_ascii=False,indent=2))
    finally: await db.dispose()

if __name__=='__main__': asyncio.run(main())
