from __future__ import annotations

import asyncio
import json

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_postgres_unit_of_work, create_mysql_validation_unit_of_work


async def main():
    pg = create_postgres_unit_of_work()
    mysql = create_mysql_validation_unit_of_work(get_settings().mysql_url)
    try:
        result = {
            "postgres_profile_save": hasattr(pg.profiles, "save"),
            "postgres_profile_update_values": hasattr(pg.profiles, "update_values"),
            "postgres_asset_save": hasattr(pg.assets, "save"),
            "mysql_profile_save": hasattr(mysql.profiles, "save"),
            "mysql_profile_update_values": hasattr(mysql.profiles, "update_values"),
            "mysql_asset_save": hasattr(mysql.assets, "save"),
        }
        result["pass"] = all(result.values())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        await mysql.close()


if __name__ == "__main__":
    asyncio.run(main())
