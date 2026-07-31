from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.db.session import Database
from app.models.auth import User, Role
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, Product, ProductSuitabilityRule, RiskRule
from app.models.trading import Account

async def ensure_profile_seed(database: Database) -> None:
 async with database.session_factory() as s:
  user=(await s.execute(select(User).options(selectinload(User.roles)).where(User.username=="customer_demo"))).scalar_one_or_none()
  if user is None:return
  customer_role=(await s.execute(select(Role).where(Role.code=="customer"))).scalar_one_or_none()
  if customer_role is None or customer_role not in user.roles:return
  product=(await s.execute(select(Product).where(Product.name=="稳健增值计划"))).scalar_one_or_none()
  if product is None:
   product=Product(id=str(uuid4()),name="稳健增值计划",product_type="fund",risk_level="C2",term_days=365,minimum_amount=10000,liquidity="medium",description="构造稳健型产品",status="active",source_type="synthetic");s.add(product);await s.flush()
  profile=(await s.execute(select(CustomerProfile).where(CustomerProfile.user_id==user.id))).scalar_one_or_none()
  if profile is None:s.add(CustomerProfile(id=str(uuid4()),user_id=user.id,age=35,occupation="企业管理",region="上海",customer_type="individual",investment_experience_years=5,investment_goal="稳健增值",investment_horizon="3-5年",liquidity_preference="中等",source_type="synthetic"))
  asset=(await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id))).scalar_one_or_none()
  account=(await s.execute(select(Account).where(Account.user_id==user.id))).scalar_one_or_none()
  if account is None:s.add(Account(id=str(uuid4()),user_id=user.id,account_no=f"AC{user.id[:10].upper()}",available_balance=200000,frozen_balance=0,status="active"))
  if asset is None:s.add(CustomerAssetSnapshot(id=str(uuid4()),user_id=user.id,total_asset=800000,cash_balance=200000,investable_asset=600000,liability=50000,net_asset=750000,source_type="synthetic"))
  holding=(await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user.id))).scalar_one_or_none()
  rule=(await s.execute(select(ProductSuitabilityRule).where(ProductSuitabilityRule.product_id==product.id))).scalar_one_or_none()
  if rule is None:s.add(ProductSuitabilityRule(id=str(uuid4()),product_id=product.id,minimum_risk_level="C2",investor_type="individual",minimum_investable_asset=10000,rule_text="C2及以上客户且可投资资产达到1万元"))
  risk=(await s.execute(select(RiskRule).where(RiskRule.rule_code=="SUITABILITY-RISK-001"))).scalar_one_or_none()
  if risk is None:s.add(RiskRule(id=str(uuid4()),rule_code="SUITABILITY-RISK-001",name="客户与产品风险等级匹配",rule_type="suitability",config_json="{\"customer_level\":\"C1-C5\",\"product_level\":\"R1-R5\"}",risk_level="medium",source_document="BUSINESS_KNOWLEDGE_PRODUCTS.md"))
  if holding is None:s.add(CustomerHolding(id=str(uuid4()),user_id=user.id,product_id=product.id,quantity=100,cost_amount=100000,market_value=105000,profit_loss=5000,holding_days=120,status="active"))
  await s.commit()
