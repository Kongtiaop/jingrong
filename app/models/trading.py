from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

class Account(Base):
    __tablename__ = "account"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    account_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    available_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    frozen_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    risk_restriction_status: Mapped[str] = mapped_column(String(32), default="none", index=True)
    risk_restriction_reason: Mapped[str] = mapped_column(Text, default="")
    risk_restricted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending_confirmation", index=True)
    side: Mapped[str] = mapped_column(String(16), default="buy")
    review_note: Mapped[str] = mapped_column(Text, default="")
    failure_reason: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    order_source: Mapped[str] = mapped_column(String(32), default="customer_direct", index=True)
    advisor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    confirmation_mode: Mapped[str] = mapped_column(String(32), default="customer_order", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trade_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    operator_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AdvisorOperationDraft(Base):
    __tablename__ = "advisor_operation_draft"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    advisor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.id"), index=True)
    side: Mapped[str] = mapped_column(String(16), default="buy")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_customer_confirmation", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    customer_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())



class MoneyFlow(Base):
    __tablename__ = "money_flow"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("account.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    flow_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    counterparty_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(64), default="unknown")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class RiskInvestigation(Base):
    __tablename__ = "risk_investigation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("risk_alert.id", ondelete="CASCADE"), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    conclusion: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RiskReportDraft(Base):
    __tablename__ = "risk_report_draft"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("risk_alert.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    report_type: Mapped[str] = mapped_column(String(32), default="large_transaction")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskAlert(Base):
    __tablename__ = "risk_alert"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)
    rule_code: Mapped[str] = mapped_column(String(64), index=True)
    alert_level: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), default=1)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    handled_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    handled_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assessment_result: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    assessment_note: Mapped[str] = mapped_column(Text, default="")
    customer_consulted: Mapped[bool] = mapped_column(default=False)
    customer_consultation_note: Mapped[str] = mapped_column(Text, default="")
    disposition_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    disposition_note: Mapped[str] = mapped_column(Text, default="")
    action_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_status: Mapped[str] = mapped_column(String(32), default="not_started", index=True)
    action_note: Mapped[str] = mapped_column(Text, default="")
    investigation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    report_draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    restriction_status: Mapped[str] = mapped_column(String(32), default="none", index=True)
    restriction_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)







class AmlBatchRun(Base):
    __tablename__ = "aml_batch_run"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    scan_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scan_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scan_days: Mapped[int] = mapped_column(default=7)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    total_customers: Mapped[int] = mapped_column(default=0)
    succeeded_customers: Mapped[int] = mapped_column(default=0)
    failed_customers: Mapped[int] = mapped_column(default=0)
    created_alerts: Mapped[int] = mapped_column(default=0)
    error_summary: Mapped[str] = mapped_column(Text, default="")
    run_type: Mapped[str] = mapped_column(String(32), default="daily", nullable=False, index=True)
    source_batch_id: Mapped[str | None] = mapped_column(ForeignKey("aml_batch_run.id", ondelete="SET NULL"), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AmlBatchAlert(Base):
    __tablename__ = "aml_batch_alert"
    batch_id: Mapped[str] = mapped_column(ForeignKey("aml_batch_run.id", ondelete="CASCADE"), primary_key=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("risk_alert.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AmlSchedulerState(Base):
    __tablename__ = "aml_scheduler_state"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_status: Mapped[str] = mapped_column(String(32), default="never", nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
