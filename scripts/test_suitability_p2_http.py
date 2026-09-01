from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.settings import get_settings
from app.db.session import Database
from app.main import app, lifespan
from app.models.profile import CustomerAssetSnapshot, CustomerProfile, CustomerRiskAssessment, Product, ProductSuitabilityRule
from app.models.trading import Account

BASE = "http://test/api/v1"
CUSTOMER = {"username": "customer_demo", "password": "Demo@2026Customer"}


async def login(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/auth/login", json=CUSTOMER)
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def main() -> None:
    database = Database(get_settings())
    created_ids: list[tuple[type, str]] = []
    try:
        async with lifespan(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                headers = await login(client)
                async with database.session_factory() as session:
                    from app.models.auth import User

                    customer = (await session.execute(select(User).where(User.username == CUSTOMER["username"]))).scalar_one()
                    profile = (await session.execute(select(CustomerProfile).where(CustomerProfile.user_id == customer.id))).scalar_one_or_none()
                    if profile is None:
                        profile = CustomerProfile(id="p2-profile-fixture", user_id=customer.id, customer_type="individual")
                        session.add(profile)
                        created_ids.append((CustomerProfile, profile.id))
                    risk = (await session.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id == customer.id, CustomerRiskAssessment.status == "active"))).scalars().first()
                    if risk is None:
                        risk = CustomerRiskAssessment(id="p2-risk-fixture", user_id=customer.id, risk_level="C3", score=60, status="active")
                        session.add(risk)
                        created_ids.append((CustomerRiskAssessment, risk.id))
                    else:
                        risk.risk_level = "C3"
                    asset = (await session.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id == customer.id).order_by(CustomerAssetSnapshot.created_at.desc()).limit(1))).scalars().first()
                    if asset is None:
                        asset = CustomerAssetSnapshot(id="p2-asset-fixture", user_id=customer.id, total_asset=100000, cash_balance=50000, investable_asset=50000, liability=0, net_asset=100000)
                        session.add(asset)
                        created_ids.append((CustomerAssetSnapshot, asset.id))
                    else:
                        asset.total_asset = 100000
                        asset.investable_asset = 50000
                    account = (await session.execute(select(Account).where(Account.user_id == customer.id))).scalar_one_or_none()
                    if account is not None:
                        account.risk_restriction_status = "none"
                        account.risk_restriction_reason = ""
                    matched = Product(id="p2-suitability-match", name="P2 适配产品", product_type="fund", risk_level="C2", minimum_amount=10000, liquidity="medium", target_customer_type="individual", status="active")
                    excluded = Product(id="p2-suitability-excluded", name="P2 高风险产品", product_type="fund", risk_level="C5", minimum_amount=80000, liquidity="low", target_customer_type="individual", status="active")
                    session.add_all([matched, excluded])
                    session.add_all([
                        ProductSuitabilityRule(id="p2-rule-match", product_id=matched.id, minimum_risk_level="C2", investor_type="individual", minimum_investable_asset=10000, rule_text="C2 及以上且资产达到 1 万元"),
                        ProductSuitabilityRule(id="p2-rule-excluded", product_id=excluded.id, minimum_risk_level="C5", investor_type="individual", minimum_investable_asset=80000, rule_text="C5 且资产达到 8 万元"),
                    ])
                    created_ids.extend([(ProductSuitabilityRule, "p2-rule-match"), (ProductSuitabilityRule, "p2-rule-excluded"), (Product, matched.id), (Product, excluded.id)])
                    await session.commit()

                before = await client.get("/trading/orders/me", headers=headers)
                before.raise_for_status()
                before_count = len(before.json()["data"]["items"])

                response = await client.get("/profile/me/recommendations", headers=headers)
                response.raise_for_status()
                data = response.json()["data"]
                assert data["context"]["risk_level"] == "C3", data
                assert data["context"]["investable_asset"] >= 50000.0, data
                assert any(item["product_id"] == "p2-suitability-match" for item in data["matches"]), data
                excluded_row = next(item for item in data["excluded"] if item["product_id"] == "p2-suitability-excluded")
                assert any("高于当前客户风险等级" in reason for reason in excluded_row["reasons"]), excluded_row
                assert excluded_row["minimum_investable_asset"] == 80000.0, excluded_row
                assert excluded_row["suitability_rule"] == "C5 且资产达到 8 万元", excluded_row

                after = await client.get("/trading/orders/me", headers=headers)
                after.raise_for_status()
                assert len(after.json()["data"]["items"]) == before_count

                print({"risk_context": True, "matched_product": True, "excluded_product": True, "rule_explanation": True, "read_only": True, "pass": True})
    finally:
        async with database.session_factory() as session:
            for model, row_id in reversed(created_ids):
                row = await session.get(model, row_id)
                if row is not None:
                    await session.delete(row)
            await session.commit()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
