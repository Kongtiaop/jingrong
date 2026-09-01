from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BadCase(Base):
    """线上 bad case 持久化（飞轮"积累"落库）。

    由 bad_case_worker 从 logs/bad_cases.jsonl 消费落库；query_hash 唯一去重。
    """
    __tablename__ = "bad_case"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    intent: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_status: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    handoff: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clarifications: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)  # pending/reviewed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    # 人工标注字段（飞轮"标注"闭环：bad case → 标注 → 导出进黄金评测集）
    labeled_intent: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    requires_clarification: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    split: Mapped[str] = mapped_column(String(16), default="train", nullable=False)
    labeled_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_bad_case_status_created", "status", "created_at"),
    )
