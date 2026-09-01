"""add bad_case table (线上 bad case 落库，数据飞轮积累)"""
from alembic import op
import sqlalchemy as sa

revision = "0023_bad_case"
down_revision = "0022_nl_order_draft"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bad_case",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("customer_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("intent", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence_status", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("handoff", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("clarifications", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_bad_case_customer_id", "bad_case", ["customer_id"])
    op.create_index("ix_bad_case_status", "bad_case", ["status"])
    op.create_index("ix_bad_case_created_at", "bad_case", ["created_at"])
    op.create_index("ix_bad_case_status_created", "bad_case", ["status", "created_at"])


def downgrade():
    for name in ("ix_bad_case_status_created", "ix_bad_case_created_at", "ix_bad_case_status", "ix_bad_case_customer_id"):
        op.drop_index(name, table_name="bad_case")
    op.drop_table("bad_case")
