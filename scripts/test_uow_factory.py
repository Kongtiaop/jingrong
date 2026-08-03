from __future__ import annotations

import asyncio
import json

from app.core.settings import Settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work, create_postgres_unit_of_work

async def main():
    pg = create_postgres_unit_of_work()
    settings = Settings(database_backend="mysql", database_write_backend="postgresql", mysql_validation_writes=True)
    mysql = create_mysql_validation_unit_of_work(settings.mysql_url)
    try:
        await mysql.database.fetch_one("SELECT 1 AS ok")
        print(json.dumps({"postgres_uow": type(pg).__name__, "mysql_adapter": type(mysql).__name__, "mysql_connection": True, "pass": True}, ensure_ascii=False, indent=2))
    finally:
        await mysql.close()
if __name__ == '__main__': asyncio.run(main())
