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
            "postgres_audit_save": hasattr(pg.audit_logs, "save"),
            "mysql_audit_save": hasattr(mysql.audit_logs, "save"),
        }
        result["pass"] = all(result.values())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["pass"]:
            raise SystemExit(1)
    finally:
        await mysql.close()


if __name__ == "__main__":
    asyncio.run(main())
