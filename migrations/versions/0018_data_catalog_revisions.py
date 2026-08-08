"""add data catalog revision history"""
from alembic import op
import sqlalchemy as sa


revision = "0018_data_catalog_revisions"
down_revision = "0017_data_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_catalog_revision",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False, server_default="updated"),
        sa.Column("snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("changed_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("resource_type", "resource_id", "revision_no", name="uq_data_catalog_revision_number"),
    )
    op.create_index("ix_data_catalog_revision_resource_type", "data_catalog_revision", ["resource_type"])
    op.create_index("ix_data_catalog_revision_resource_id", "data_catalog_revision", ["resource_id"])
    op.create_index("ix_data_catalog_revision_changed_by", "data_catalog_revision", ["changed_by"])


def downgrade():
    op.drop_table("data_catalog_revision")
