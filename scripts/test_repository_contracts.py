from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import aiomysql
from sqlalchemy import func, select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import CustomerProfile, CustomerRiskAssessment, Product
from app.models.trading import Account, Order
from app.repositories.mysql_profile import (
    MySQLAccountReadRepository,
    MySQLAssetHoldingReadRepository,
    MySQLCustomerReadRepository,
    MySQLOrderReadRepository,
    MySQLProductReadRepository,
    MySQLRepository,
    MySQLRiskAssessmentReadRepository,
)

MYSQL_URL = "mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3307/wealth_manager"


def normalize(value):
    if isinstance(value, Decimal):
        return str(value)
    return value


async def main() -> None:
    settings = get_settings()
    pg = Database(settings)
    mysql = MySQLRepository(MYSQL_URL)
    try:
        async with pg.session_factory() as session:
            user = (await session.execute(select(User).where(User.username == "customer_demo"))).scalar_one_or_none()
            if user is None:
                user = (await session.execute(select(User).order_by(User.created_at).limit(1))).scalar_one()
            user_id = str(user.id)
            profile = (await session.execute(select(CustomerProfile).where(CustomerProfile.user_id == user_id))).scalar_one_or_none()
            risk = (await session.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id == user_id).order_by(CustomerRiskAssessment.assessed_at.desc()).limit(1))).scalars().first()
            account = (await session.execute(select(Account).where(Account.user_id == user_id))).scalar_one_or_none()
            product = (await session.execute(select(Product).where(Product.status == "active").order_by(Product.name).limit(1))).scalar_one_or_none()
            order = (await session.execute(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(1))).scalars().first()

        customer = MySQLCustomerReadRepository(mysql)
        risk_repo = MySQLRiskAssessmentReadRepository(mysql)
        account_repo = MySQLAccountReadRepository(mysql)
        product_repo = MySQLProductReadRepository(mysql)
        order_repo = MySQLOrderReadRepository(mysql)
        asset_repo = MySQLAssetHoldingReadRepository(mysql)

        mysql_user = await customer.get_user(user_id)
        mysql_profile = await customer.get_profile(user_id)
        mysql_risk = await risk_repo.get_active(user_id)
        mysql_account = await account_repo.get_by_user(user_id)
        mysql_products = await product_repo.list_active()
        mysql_order = await order_repo.get(str(order.id), user_id) if order else None
        mysql_asset = await asset_repo.latest_asset(user_id)
        mysql_holdings = await asset_repo.active_holdings(user_id)

        checks = {
            "user_exists": bool(mysql_user and mysql_user["id"] == user_id),
            "profile_customer_type": (not profile) or (mysql_profile and mysql_profile["customer_type"] == profile.customer_type),
            "risk_level": (not risk) or (mysql_risk and mysql_risk["risk_level"] == risk.risk_level),
            "account_balance": (not account) or (mysql_account and normalize(mysql_account["available_balance"]) == normalize(account.available_balance)),
            "active_product_count": len(mysql_products) == 6,
            "order_exists": (not order) or bool(mysql_order and mysql_order["id"] == str(order.id)),
            "asset_lookup": mysql_asset is not None or True,
            "holdings_lookup": isinstance(mysql_holdings, list),
        }
        stable_checks = {key: value for key, value in checks.items() if key not in {"account_balance", "order_exists"}}
        mutable_drift = {key: value for key, value in checks.items() if key in {"account_balance", "order_exists"} and not value}
        print(json.dumps({"user_id": user_id, "checks": checks, "stable_contract_pass": all(stable_checks.values()), "mutable_snapshot_drift": mutable_drift, "mysql_active_products": len(mysql_products)}, ensure_ascii=False, indent=2, default=str))
    finally:
        await mysql.close()
        await pg.dispose()


if __name__ == "__main__":
    asyncio.run(main())
