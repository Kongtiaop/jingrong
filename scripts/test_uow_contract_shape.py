from __future__ import annotations

import asyncio
import json

from app.core.settings import get_settings
from app.repositories.contracts import TradingUnitOfWorkProtocol
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work, create_postgres_unit_of_work

REQUIRED = ["users", "profiles", "risks", "products", "accounts", "orders", "holdings", "assets", "trades", "status_history"]

async def main():
    pg = create_postgres_unit_of_work()
    mysql = create_mysql_validation_unit_of_work(get_settings().mysql_url)
    try:
        result = {
            "protocol": TradingUnitOfWorkProtocol.__name__,
            "postgres": {name: hasattr(pg, name) for name in REQUIRED},
            "mysql": {"database": hasattr(mysql, "database"), "transaction": callable(getattr(mysql, "transaction", None)), "close": callable(getattr(mysql, "close", None))},
        }
        result["pass"] = all(result["postgres"].values()) and all(result["mysql"].values())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["pass"]: raise SystemExit(1)
    finally:
        await mysql.close()

if __name__ == "__main__": asyncio.run(main())
