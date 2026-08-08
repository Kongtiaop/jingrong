"""create work order MVP table"""
from alembic import op
import sqlalchemy as sa

revision = "0010_work_order"
down_revision = "0009_aml_batch"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "work_order",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_no", sa.String(64), nullable=False, unique=True),
        sa.Column("customer_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False),
        sa.Column("assigned_to", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_work_order_customer_id", "work_order", ["customer_id"])
    op.create_index("ix_work_order_created_by", "work_order", ["created_by"])
    op.create_index("ix_work_order_assigned_to", "work_order", ["assigned_to"])
    op.create_index("ix_work_order_category", "work_order", ["category"])
    op.create_index("ix_work_order_priority", "work_order", ["priority"])
    op.create_index("ix_work_order_status", "work_order", ["status"])
    op.create_index("ix_work_order_customer_status", "work_order", ["customer_id", "status"])


def downgrade():
    op.drop_table("work_order")
