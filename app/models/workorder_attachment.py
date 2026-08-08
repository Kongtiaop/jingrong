from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkOrderAttachment(Base):
    __tablename__ = "work_order_attachment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_order.id", ondelete="CASCADE"), nullable=False, index=True)
    update_id: Mapped[str | None] = mapped_column(ForeignKey("work_order_update.id", ondelete="SET NULL"), nullable=True, index=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_work_order_attachment_work_order_created", "work_order_id", "created_at"),
    )
