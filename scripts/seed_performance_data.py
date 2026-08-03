"""Seed repeatable synthetic customer data for local performance tests."""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select, text
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.db.schema import ensure_schema
from app.db.session import Database
from app.models.auth import Role, User, user_roles
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, CustomerRiskAssessment, Product

PREFIX = "perf_customer_"

async def seed(database: Database, count: int) -> dict[str, int]:
    async with database.session_factory() as session:
        role = (await session.execute(select(Role).where(Role.code == "customer"))).scalar_one()
        product = (await session.execute(select(Product).where(Product.name == "稳健增值计划"))).scalar_one()
        existing = list((await session.execute(select(User).where(User.username.like(f"{PREFIX}%")))).scalars().all())
        created = 0
        for i in range(count):
            username = f"{PREFIX}{i:04d}"
            user = next((row for row in existing if row.username == username), None)
            if user is None:
                user = User(id=str(uuid4()), username=username, password_hash="", display_name=f"性能客户{i:04d}", status="active", is_super_admin=False)
                user.roles.append(role)
                session.add(user)
                await session.flush()
                created += 1
            profile = (await session.execute(select(CustomerProfile).where(CustomerProfile.user_id == user.id))).scalar_one_or_none()
            if profile is None:
                await session.execute(text("INSERT INTO customer_profile (id,user_id,age,occupation,region,customer_type,customer_tier,investment_experience_years,investment_goal,investment_horizon,liquidity_preference,source_type,investment_horizon_years) VALUES (:id,:user_id,:age,:occupation,:region,:customer_type,:customer_tier,:experience,:goal,:horizon,:liquidity,:source,:horizon_years)"), {"id": str(uuid4()), "user_id": user.id, "age": 25 + i % 40, "occupation": "性能测试", "region": "上海", "customer_type": "individual", "customer_tier": "ordinary", "experience": i % 10, "goal": "稳健增值", "horizon": "3-5年", "liquidity": "medium", "source": "performance_seed", "horizon_years": 3})
            asset = (await session.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id == user.id))).scalars().first()
            if asset is None:
                total = Decimal(100000 + i * 1000)
                session.add(CustomerAssetSnapshot(id=str(uuid4()), user_id=user.id, total_asset=total, cash_balance=total / 2, investable_asset=total / 2, liability=0, net_asset=total, source_type="performance_seed"))
            risk = (await session.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id == user.id))).scalars().first()
            if risk is None:
                session.add(CustomerRiskAssessment(id=str(uuid4()), user_id=user.id, risk_level=f"C{1 + i % 5}", status="active", source_type="performance_seed"))
            holding = (await session.execute(select(CustomerHolding).where(CustomerHolding.user_id == user.id))).scalars().first()
            if holding is None:
                session.add(CustomerHolding(id=str(uuid4()), user_id=user.id, product_id=product.id, quantity=Decimal(10 + i % 100), cost_amount=Decimal(10000 + i * 100), market_value=Decimal(10500 + i * 100), profit_loss=Decimal(500), holding_days=30 + i % 300, status="active"))
        await session.commit()
        return {"requested": count, "created": created, "total_perf_users": count if created else len(existing)}

async def cleanup(database: Database) -> int:
    async with database.engine.begin() as connection:
        result = await connection.execute(delete(User).where(User.username.like(f"{PREFIX}%")))
        return result.rowcount or 0

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.count < 1 or args.count > 5000: raise SystemExit("count must be between 1 and 5000")
    settings = get_settings()
    if settings.app_env not in {"development", "dev", "local"}: raise SystemExit("performance seed is development-only")
    database = Database(settings)
    try:
        await ensure_schema(database.engine)
        if args.cleanup: print({"deleted": await cleanup(database)})
        else: print(await seed(database, args.count))
    finally: await database.dispose()

if __name__ == "__main__": asyncio.run(main())











