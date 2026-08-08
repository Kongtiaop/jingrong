"""add AML batch run records"""

from alembic import op
import sqlalchemy as sa

revision = "0009_aml_batch"
down_revision = "0008_profile_horizon"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aml_batch_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_key", sa.String(160), nullable=False, unique=True),
        sa.Column("scan_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("total_customers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("succeeded_customers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_customers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_alerts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_aml_batch_run_scan_window_start", "aml_batch_run", ["scan_window_start"])
    op.create_index("ix_aml_batch_run_scan_window_end", "aml_batch_run", ["scan_window_end"])
    op.create_index("ix_aml_batch_run_status", "aml_batch_run", ["status"])


def downgrade():
    op.drop_table("aml_batch_run")
