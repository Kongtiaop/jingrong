from __future__ import annotations

import asyncio

from app.core.settings import get_settings
from app.db.schema import ensure_schema
from app.db.session import Database
from app.services.data_catalog_seed import ensure_data_catalog_seed


async def main() -> None:
    database = Database(get_settings())
    try:
        await ensure_schema(database.engine)
        await ensure_data_catalog_seed(database)
        print({"schema_and_seed": "PASS"})
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
