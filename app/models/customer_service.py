from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class CustomerServiceSession(Base):
    __tablename__ = "customer_service_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    work_order_id: Mapped[str | None] = mapped_column(ForeignKey("work_order.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="waiting_human", index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="human")
    assigned_agent_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    handoff_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="customer_requested")
    handoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_customer_service_session_customer_status", "customer_id", "status"),
        Index("uq_customer_service_session_active_agent", "assigned_agent_id", unique=True, postgresql_where=text("status = 'human_active'")),
    )


class CustomerServiceMessage(Base):
    __tablename__ = "customer_service_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("customer_service_session.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="workflow")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (Index("ix_customer_service_message_session_created", "session_id", "created_at"),)


class AgentConversationMessage(Base):
    """AI 对话消息（问答留痕）。

    与 `CustomerServiceMessage` 的关键区别：后者**只在转人工之后**产生，
    本表覆盖 AI 阶段的每一次问答。

    为什么需要它：此前 AI 问答内容**无处留存**——`audit_logs` 里只有工具调用轨迹
    （且 `output_preview` 仅存结构性摘要，如"命中 5 条知识块"，没有答案全文），
    导致刷新页面后对话即消失，用户既看不到历史、也无从回溯"当时问了什么、答了什么"。
    """

    __tablename__ = "agent_conversation_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user / assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 关联本次请求的 trace_id：把一条回答与它的工具链审计串起来，
    # 便于日后按 trace 精确取回"这一条回答当时调了哪些工具"（/trace 是按 conversation 查的）。
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 以下仅 assistant 消息有：供前端还原"当时是怎么答的"
    overall_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    route: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 nullable=False, index=True)

    __table_args__ = (
        Index("ix_agent_conversation_message_conv_created", "conversation_id", "created_at"),
    )
