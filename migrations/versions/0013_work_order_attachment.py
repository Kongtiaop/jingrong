"""create work order attachment table"""
from alembic import op
import sqlalchemy as sa

revision = "0013_work_order_attachment"
down_revision = "0012_work_order_update"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "work_order_attachment",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("work_order_id", sa.String(36), sa.ForeignKey("work_order.id", ondelete="CASCADE"), nullable=False),
        sa.Column("update_id", sa.String(36), sa.ForeignKey("work_order_update.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("stored_name", sa.String(255), nullable=False, unique=True),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_work_order_attachment_work_order_id", "work_order_attachment", ["work_order_id"])
    op.create_index("ix_work_order_attachment_update_id", "work_order_attachment", ["update_id"])
    op.create_index("ix_work_order_attachment_uploaded_by", "work_order_attachment", ["uploaded_by"])
    op.create_index("ix_work_order_attachment_created_at", "work_order_attachment", ["created_at"])
    op.create_index("ix_work_order_attachment_work_order_created", "work_order_attachment", ["work_order_id", "created_at"])


def downgrade():
    op.drop_table("work_order_attachment")
