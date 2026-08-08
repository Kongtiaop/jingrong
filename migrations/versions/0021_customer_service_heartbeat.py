"""add customer service agent heartbeat timestamp"""
from alembic import op
import sqlalchemy as sa
revision = "0021_customer_service_heartbeat"
down_revision = "0020_customer_memory"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("customer_service_session", sa.Column("agent_last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_customer_service_session_agent_last_heartbeat_at", "customer_service_session", ["agent_last_heartbeat_at"])
def downgrade():
    op.drop_index("ix_customer_service_session_agent_last_heartbeat_at", table_name="customer_service_session")
    op.drop_column("customer_service_session", "agent_last_heartbeat_at")
