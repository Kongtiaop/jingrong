"""add AML scheduler heartbeat state"""

from alembic import op
import sqlalchemy as sa

revision = "0015_aml_scheduler_state"
down_revision = "0014_work_order_risk_alert_link"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aml_scheduler_state",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_scan_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_scan_finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_scan_status", sa.String(32), nullable=False, server_default="never"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade():
    op.drop_table("aml_scheduler_state")
