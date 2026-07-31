from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import Database
from app.models.auth import Role, User
from app.services.auth_service import hash_password
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, CustomerRiskAssessment, Product
from app.models.trading import Account
from app.services.risk_assessment_service import RiskAssessmentService


DEMO_CUSTOMERS = (
    {
        "username": "hNW_demo",
        "display_name": "高净值客户演示",
        "customer_type": "individual",
        "customer_tier": "private_bank",
        "asset": Decimal("12000000"),
        "cash": Decimal("4000000"),
        "investable": Decimal("12000000"),
        "goal": "资产保值与传承",
    },
    {
        "username": "enterprise_demo",
        "display_name": "企业客户演示",
        "customer_type": "enterprise",
        "customer_tier": "enterprise_standard",
        "asset": Decimal("5000000"),
        "cash": Decimal("3000000"),
        "investable": Decimal("5000000"),
        "goal": "企业资金稳健配置",
    },
)


async def ensure_demo_customer_profiles(database: Database) -> None:
    async with database.session_factory() as session:
        role = (await session.execute(select(Role).where(Role.code == "customer"))).scalar_one_or_none()
        if role is None:
            return
        product = (await session.execute(select(Product).where(Product.name == "稳健增值计划"))).scalar_one_or_none()
        if product is None:
            return
        for item in DEMO_CUSTOMERS:
            user = (await session.execute(select(User).options(selectinload(User.roles)).where(User.username == item["username"]))).scalar_one_or_none()
            if user is None:
                user = User(
                    id=str(uuid4()),
                    username=item["username"],
                    password_hash=hash_password("Demo@2026HighNetWorth" if item["username"] == "hNW_demo" else "Demo@2026Enterprise"),
                    display_name=item["display_name"],
                    status="active",
                    is_super_admin=False,
                )
                session.add(user)
                await session.flush()
            await session.refresh(user, attribute_names=["roles"])
            if role not in user.roles:
                user.roles.append(role)
            profile = (await session.execute(select(CustomerProfile).where(CustomerProfile.user_id == user.id))).scalar_one_or_none()
            if profile is None:
                profile = CustomerProfile(id=str(uuid4()), user_id=user.id)
                session.add(profile)
            profile.customer_type = item["customer_type"]
            profile.customer_tier = item["customer_tier"]
            profile.investment_goal = item["goal"]
            profile.investment_horizon = "5年以上"
            profile.liquidity_preference = "中等"
            profile.source_type = "synthetic_demo"
            account = (await session.execute(select(Account).where(Account.user_id == user.id))).scalar_one_or_none()
            if account is None:
                account = Account(id=str(uuid4()), user_id=user.id, account_no=f"AC{user.id[:10].upper()}", currency="CNY", status="active")
                session.add(account)
            account.available_balance = item["cash"]
            account.frozen_balance = Decimal("0")
            asset = (await session.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id == user.id).order_by(CustomerAssetSnapshot.snapshot_time.desc().nullslast(), CustomerAssetSnapshot.id.desc()).limit(1))).scalars().first()
            if asset is None:
                asset = CustomerAssetSnapshot(id=str(uuid4()), user_id=user.id)
                session.add(asset)
            asset.total_asset = item["asset"]
            asset.cash_balance = item["cash"]
            asset.investable_asset = item["investable"]
            asset.liability = Decimal("0")
            asset.net_asset = item["asset"]
            asset.source_type = "synthetic_demo"
            holding = (await session.execute(select(CustomerHolding).where(CustomerHolding.user_id == user.id, CustomerHolding.product_id == product.id, CustomerHolding.status == "active").limit(1))).scalars().first()
            if holding is None:
                holding = CustomerHolding(id=str(uuid4()), user_id=user.id, product_id=product.id, quantity=item["asset"] - item["cash"], cost_amount=item["asset"] - item["cash"], market_value=item["asset"] - item["cash"], profit_loss=Decimal("0"), holding_days=90, status="active")
                session.add(holding)
        demo_risks = {"hNW_demo": "C3", "enterprise_demo": "C2"}
        for username, level in demo_risks.items():
            demo_user = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if demo_user is None: continue
            risk = (await session.execute(select(CustomerRiskAssessment).where(CustomerRiskAssessment.user_id == demo_user.id, CustomerRiskAssessment.status == "active").order_by(CustomerRiskAssessment.assessed_at.desc()).limit(1))).scalars().first()
            if risk is None:
                answers = {"investment_experience_years": 5, "max_loss_tolerance": 30, "investment_horizon_years": 5, "liquidity_need": "medium", "investment_goal": "balanced", "risk_willingness": 40}
                if level == "C3": answers["risk_willingness"] = 60
                risk = CustomerRiskAssessment(id=str(uuid4()), user_id=demo_user.id, risk_level=level, score=50 if level == "C3" else 35, answers_json=__import__("json").dumps(answers, ensure_ascii=False), status="active", source_type="synthetic_demo")
                session.add(risk)
        await session.commit()
