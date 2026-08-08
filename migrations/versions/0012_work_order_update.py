"""create work order update table"""
from alembic import op
import sqlalchemy as sa

revision = "0012_work_order_update"
down_revision = "0011_notification"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "work_order_update",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_order_id", sa.String(36), sa.ForeignKey("work_order.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False),
        sa.Column("author_role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_work_order_update_work_order_id", "work_order_update", ["work_order_id"])
    op.create_index("ix_work_order_update_author_user_id", "work_order_update", ["author_user_id"])
    op.create_index("ix_work_order_update_author_role", "work_order_update", ["author_role"])
    op.create_index("ix_work_order_update_created_at", "work_order_update", ["created_at"])
    op.create_index("ix_work_order_update_work_order_created", "work_order_update", ["work_order_id", "created_at"])


def downgrade():
    op.drop_table("work_order_update")
