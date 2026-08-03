from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import delete, select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.profile import CustomerEnterpriseVerification


async def main(snapshot_file: str | None, yes: bool):
    settings = get_settings()
    if settings.app_env.lower() not in {"development", "dev", "local"}:
        raise RuntimeError("admin smoke cleanup is only allowed in development/dev/local")
    if not yes:
        raise RuntimeError("refusing cleanup without --yes")
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            query = select(CustomerEnterpriseVerification.id).where(
                CustomerEnterpriseVerification.company_name.like("Admin Smoke Enterprise %"),
                CustomerEnterpriseVerification.registration_no == "ADMIN-SMOKE",
                CustomerEnterpriseVerification.status == "rejected",
            )
            ids = [row[0] for row in (await session.execute(query)).all()]
            if ids:
                result = await session.execute(delete(CustomerEnterpriseVerification).where(CustomerEnterpriseVerification.id.in_(ids)))
                await session.commit()
                deleted = result.rowcount or 0
            else:
                deleted = 0
            print(json.dumps({"matched_ids": ids, "deleted": deleted, "pass": True}, ensure_ascii=False, indent=2))
    finally:
        await database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-file")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.snapshot_file, args.yes))
