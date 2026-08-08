"""add customer service session and message state foundation"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019_customer_service_session"
down_revision = "0018_data_catalog_revisions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_service_session",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("work_order_id", sa.String(36), sa.ForeignKey("work_order.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="waiting_human"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="human"),
        sa.Column("assigned_agent_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("handoff_reason", sa.String(64), nullable=False, server_default="customer_requested"),
        sa.Column("handoff_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_customer_service_session_customer_id", "customer_service_session", ["customer_id"])
    op.create_index("ix_customer_service_session_work_order_id", "customer_service_session", ["work_order_id"])
    op.create_index("ix_customer_service_session_status", "customer_service_session", ["status"])
    op.create_index("ix_customer_service_session_assigned_agent_id", "customer_service_session", ["assigned_agent_id"])
    op.create_index("ix_customer_service_session_customer_status", "customer_service_session", ["customer_id", "status"])
    op.create_index("uq_customer_service_session_active_agent", "customer_service_session", ["assigned_agent_id"], unique=True, postgresql_where=sa.text("status = 'human_active'"))

    op.create_table(
        "customer_service_message",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("customer_service_session.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_type", sa.String(32), nullable=False),
        sa.Column("sender_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(32), nullable=False, server_default="text"),
        sa.Column("source", sa.String(32), nullable=False, server_default="workflow"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_customer_service_message_session_id", "customer_service_message", ["session_id"])
    op.create_index("ix_customer_service_message_sender_user_id", "customer_service_message", ["sender_user_id"])
    op.create_index("ix_customer_service_message_created_at", "customer_service_message", ["created_at"])
    op.create_index("ix_customer_service_message_session_created", "customer_service_message", ["session_id", "created_at"])


def downgrade():
    op.drop_table("customer_service_message")
    op.drop_index("uq_customer_service_session_active_agent", table_name="customer_service_session")
    op.drop_table("customer_service_session")
