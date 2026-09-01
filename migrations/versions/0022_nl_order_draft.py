"""add natural language order drafts"""
from alembic import op
import sqlalchemy as sa

revision = "0022_nl_order_draft"
down_revision = "0021_customer_service_heartbeat"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "natural_language_order_draft",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("customer_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(length=36), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False, server_default="buy"),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="awaiting_user_confirmation"),
        sa.Column("input_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("confirmation_token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("precheck_snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    for name, columns in (
        ("ix_nlod_customer_id", ["customer_id"]),
        ("ix_nlod_product_id", ["product_id"]),
        ("ix_nlod_status", ["status"]),
        ("ix_nlod_input_fingerprint", ["input_fingerprint"]),
        ("ix_nlod_confirmation_token_hash", ["confirmation_token_hash"]),
        ("ix_nlod_expires_at", ["expires_at"]),
        ("ix_nlod_order_id", ["order_id"]),
    ):
        op.create_index(name, "natural_language_order_draft", columns)


def downgrade():
    for name in ("ix_nlod_order_id", "ix_nlod_expires_at", "ix_nlod_confirmation_token_hash", "ix_nlod_input_fingerprint", "ix_nlod_status", "ix_nlod_product_id", "ix_nlod_customer_id"):
        op.drop_index(name, table_name="natural_language_order_draft")
    op.drop_table("natural_language_order_draft")
