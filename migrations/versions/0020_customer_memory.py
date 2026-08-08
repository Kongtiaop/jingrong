"""add durable customer memory ledger"""
from alembic import op
import sqlalchemy as sa

revision = "0020_customer_memory"
down_revision = "0019_customer_service_session"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "customer_memory",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_type", sa.String(32), nullable=False, server_default="conversation_fact"),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="conversation"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_customer_memory_customer_id", "customer_memory", ["customer_id"])
    op.create_index("ix_customer_memory_memory_type", "customer_memory", ["memory_type"])
    op.create_index("ix_customer_memory_key", "customer_memory", ["key"])
    op.create_index("ix_customer_memory_status", "customer_memory", ["status"])
    op.create_index("uq_customer_memory_active_key", "customer_memory", ["customer_id", "key"], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_table(
        "customer_memory_event",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("memory_id", sa.String(36), sa.ForeignKey("customer_memory.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_customer_memory_event_customer_id", "customer_memory_event", ["customer_id"])
    op.create_index("ix_customer_memory_event_conversation_id", "customer_memory_event", ["conversation_id"])
    op.create_index("ix_customer_memory_event_memory_id", "customer_memory_event", ["memory_id"])
    op.create_index("ix_customer_memory_event_event_type", "customer_memory_event", ["event_type"])
    op.create_index("ix_customer_memory_event_created_at", "customer_memory_event", ["created_at"])

def downgrade():
    op.drop_table("customer_memory_event")
    op.drop_index("uq_customer_memory_active_key", table_name="customer_memory")
    op.drop_table("customer_memory")
