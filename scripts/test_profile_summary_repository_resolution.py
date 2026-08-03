from __future__ import annotations

import asyncio
import json

from app.repositories.unit_of_work import create_postgres_unit_of_work, create_mysql_validation_unit_of_work
from app.core.settings import get_settings


async def main():
    pg = create_postgres_unit_of_work()
    mysql = create_mysql_validation_unit_of_work(get_settings().mysql_url)
    try:
        result = {
            "postgres_profile_get": hasattr(pg.profiles, "get"),
            "postgres_holdings_list_active": hasattr(pg.holdings, "list_active"),
            "postgres_assets_latest": hasattr(pg.assets, "latest"),
            "mysql_profile_get": hasattr(mysql.profiles, "get"),
            "mysql_holdings_list_active": hasattr(mysql.holdings, "list_active"),
            "mysql_assets_latest": hasattr(mysql.assets, "latest"),
        }
        result["pass"] = all(result.values())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        await mysql.close()


if __name__ == "__main__":
    asyncio.run(main())
