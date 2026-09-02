"""add shared memory semantic layer columns (scope + agent_type)"""
from alembic import op
import sqlalchemy as sa

revision = "0024_shared_memory"
down_revision = "0023_bad_case"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customer_memory", sa.Column("scope", sa.String(32), nullable=False, server_default="private"))
    op.add_column("customer_memory", sa.Column("agent_type", sa.String(32), nullable=False, server_default="customer_service"))
    op.create_index("ix_customer_memory_scope", "customer_memory", ["scope"])
    op.create_index("ix_customer_memory_agent_type", "customer_memory", ["agent_type"])
    # 唯一活跃索引改为 (customer_id, key, scope)：私有与共享同 key 可共存
    op.drop_index("uq_customer_memory_active_key", table_name="customer_memory")
    op.create_index("uq_customer_memory_active_key", "customer_memory", ["customer_id", "key", "scope"], unique=True, postgresql_where=sa.text("status = 'active'"))


def downgrade():
    op.drop_index("uq_customer_memory_active_key", table_name="customer_memory")
    op.create_index("uq_customer_memory_active_key", "customer_memory", ["customer_id", "key"], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.drop_index("ix_customer_memory_agent_type", table_name="customer_memory")
    op.drop_index("ix_customer_memory_scope", table_name="customer_memory")
    op.drop_column("customer_memory", "agent_type")
    op.drop_column("customer_memory", "scope")
