from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import delete, select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.profile import CustomerAssetSnapshot


async def main(snapshot_file: str, yes: bool):
    settings = get_settings()
    if settings.app_env.lower() not in {"development", "dev", "local"}:
        raise RuntimeError("asset smoke cleanup is only allowed in development/dev/local")
    snapshot = json.loads(Path(snapshot_file).read_text(encoding="utf-8"))
    asset = snapshot.get("latest_asset") or {}
    asset_id = asset.get("id")
    if not asset_id:
        raise ValueError("snapshot does not contain latest_asset.id")
    if not yes:
        raise RuntimeError("refusing cleanup without --yes")
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            row = (await session.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.id == asset_id))).scalar_one_or_none()
            deleted = 0
            if row is not None:
                await session.execute(delete(CustomerAssetSnapshot).where(CustomerAssetSnapshot.id == asset_id))
                await session.commit()
                deleted = 1
            print(json.dumps({"asset_id": asset_id, "deleted": deleted, "pass": True}, ensure_ascii=False, indent=2))
    finally:
        await database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-file", default="logs/profile_http_smoke_snapshot.json")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.snapshot_file, args.yes))
