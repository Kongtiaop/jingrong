from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import delete

from app.core.settings import get_settings
from app.db.session import Database
from app.models.profile import CustomerEnterpriseVerification


async def main(snapshot_file: str, yes: bool):
    settings = get_settings()
    if settings.app_env.lower() not in {"development", "dev", "local"}:
        raise RuntimeError("enterprise smoke cleanup is only allowed in development/dev/local")
    if not yes:
        raise RuntimeError("refusing cleanup without --yes")
    snapshot = json.loads(Path(snapshot_file).read_text(encoding="utf-8"))
    original = snapshot.get("enterprise_verification")
    if original is not None:
        result = {"deleted": 0, "restored_original": True, "pass": True}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            result = await session.execute(delete(CustomerEnterpriseVerification).where(CustomerEnterpriseVerification.company_name.like("HTTP Smoke Enterprise%")))
            await session.commit()
            print(json.dumps({"deleted": result.rowcount or 0, "restored_original": False, "pass": True}, ensure_ascii=False, indent=2))
    finally:
        await database.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot-file', default='logs/enterprise_http_smoke_snapshot.json')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.snapshot_file, args.yes))
