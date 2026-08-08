"""link risk material requests to work orders"""
from alembic import op
import sqlalchemy as sa

revision = "0014_work_order_risk_alert_link"
down_revision = "0013_work_order_attachment"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("work_order", sa.Column("risk_alert_id", sa.String(36), sa.ForeignKey("risk_alert.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_work_order_risk_alert_id", "work_order", ["risk_alert_id"], unique=True)


def downgrade():
    op.drop_index("ix_work_order_risk_alert_id", table_name="work_order")
    op.drop_column("work_order", "risk_alert_id")
