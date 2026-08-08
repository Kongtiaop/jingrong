"""create in-app notification table"""
from alembic import op
import sqlalchemy as sa

revision = "0011_notification"
down_revision = "0010_work_order"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notification_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_notification_user_id", "notification", ["user_id"])
    op.create_index("ix_notification_notification_type", "notification", ["notification_type"])
    op.create_index("ix_notification_resource_type", "notification", ["resource_type"])
    op.create_index("ix_notification_resource_id", "notification", ["resource_id"])
    op.create_index("ix_notification_is_read", "notification", ["is_read"])
    op.create_index("ix_notification_created_at", "notification", ["created_at"])
    op.create_index("ix_notification_user_unread_created", "notification", ["user_id", "is_read", "created_at"])


def downgrade():
    op.drop_table("notification")
