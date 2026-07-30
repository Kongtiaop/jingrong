from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class CustomerProfile(Base):
    __tablename__ = "customer_profile"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occupation: Mapped[str] = mapped_column(String(128), default="")
    region: Mapped[str] = mapped_column(String(128), default="")
    customer_type: Mapped[str] = mapped_column(String(64), default="individual")
    investment_experience_years: Mapped[int] = mapped_column(Integer, default=0)
    investment_goal: Mapped[str] = mapped_column(String(255), default="")
    investment_horizon: Mapped[str] = mapped_column(String(64), default="")
    liquidity_preference: Mapped[str] = mapped_column(String(64), default="")
    source_type: Mapped[str] = mapped_column(String(32), default="synthetic")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class CustomerSubjectiveProfile(Base):
    __tablename__ = "customer_subjective_profile"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    field_name: Mapped[str] = mapped_column(String(128), index=True)
    field_value: Mapped[str] = mapped_column(Text)
    source_text: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(32), default="user_declared")
    confirmed: Mapped[bool] = mapped_column(default=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class CustomerAssetSnapshot(Base):
    __tablename__ = "customer_asset_snapshot"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    total_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    cash_balance: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    investable_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    liability: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    net_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="synthetic")

class Product(Base):
    __tablename__ = "product"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    product_type: Mapped[str] = mapped_column(String(64), default="fund")
    risk_level: Mapped[str] = mapped_column(String(16), default="C1", index=True)
    term_days: Mapped[int] = mapped_column(Integer, default=0)
    minimum_amount: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    liquidity: Mapped[str] = mapped_column(String(64), default="medium")
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="synthetic")

class ProductSuitabilityRule(Base):
    __tablename__ = "product_suitability_rule"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.id", ondelete="CASCADE"), index=True)
    minimum_risk_level: Mapped[str] = mapped_column(String(16), default="C1")
    investor_type: Mapped[str] = mapped_column(String(64), default="individual")
    minimum_investable_asset: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    rule_text: Mapped[str] = mapped_column(Text, default="")

class RiskRule(Base):
    __tablename__ = "risk_rule"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    rule_type: Mapped[str] = mapped_column(String(32), default="suitability")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    risk_level: Mapped[str] = mapped_column(String(32), default="medium")
    enabled: Mapped[bool] = mapped_column(default=True)
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    source_document: Mapped[str] = mapped_column(String(255), default="")

class CustomerHolding(Base):
    __tablename__ = "customer_holding"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.id"), index=True)
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    cost_amount: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    market_value: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    profit_loss: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    holding_days: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="active")
