from __future__ import annotations

import asyncio
import json

import aiomysql
from sqlalchemy import func, select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerProfile, CustomerRiskAssessment, Product
from app.models.trading import Account, Order

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")


async def mysql_rows() -> dict[str, int]:
    conn = await aiomysql.connect(**MYSQL, autocommit=True)
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            out = {}
            for table, predicate in {
                "users": "username NOT IN ('mysql_probe')",
                "customer_profile": "user_id IN (SELECT id FROM users WHERE username NOT IN ('mysql_probe'))",
                "product": "1=1",
                "customer_risk_assessment": "user_id IN (SELECT id FROM users WHERE username NOT IN ('mysql_probe'))",
                "account": "user_id IN (SELECT id FROM users WHERE username NOT IN ('mysql_probe'))",
                "orders": "1=1",
            }.items():
                await cur.execute(f"SELECT COUNT(*) AS n FROM `{table}` WHERE {predicate}")
                out[table] = int((await cur.fetchone())["n"])
            return out
    finally:
        conn.close()


async def main() -> None:
    db = Database(get_settings())
    try:
        async with db.session_factory() as session:
            models = {
                "users": User,
                "customer_profile": CustomerProfile,
                "product": Product,
                "customer_risk_assessment": CustomerRiskAssessment,
                "account": Account,
                "orders": Order,
            }
            postgres = {name: int(await session.scalar(select(func.count()).select_from(model)) or 0) for name, model in models.items()}
        mysql = await mysql_rows()
        print(json.dumps({"postgres": postgres, "mysql_excluding_probe": mysql, "equal": postgres == mysql}, ensure_ascii=False, indent=2))
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
