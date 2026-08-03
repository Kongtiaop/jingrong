"""add formal order source and confirmation fields"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_order_source"
down_revision: Union[str, None] = "0002_knowledge_base"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("orders", sa.Column("order_source", sa.String(length=32), nullable=True, server_default="customer_direct"))
    op.add_column("orders", sa.Column("advisor_id", sa.String(length=36), nullable=True))
    op.add_column("orders", sa.Column("confirmation_mode", sa.String(length=32), nullable=True, server_default="customer_order"))
    op.create_index("ix_orders_order_source", "orders", ["order_source"])
    op.create_index("ix_orders_advisor_id", "orders", ["advisor_id"])
    op.create_index("ix_orders_confirmation_mode", "orders", ["confirmation_mode"])
    op.create_foreign_key("fk_orders_advisor_id_users", "orders", "users", ["advisor_id"], ["id"], ondelete="SET NULL")
    op.execute("UPDATE orders SET order_source='advisor_assisted', confirmation_mode='advisor_customer_confirmation' WHERE idempotency_key LIKE 'advisor-draft:%'")
    op.execute("UPDATE orders SET order_source='customer_direct' WHERE order_source IS NULL")
    op.execute("UPDATE orders SET confirmation_mode='customer_order' WHERE confirmation_mode IS NULL")
    op.alter_column("orders", "order_source", nullable=False, server_default="customer_direct")
    op.alter_column("orders", "confirmation_mode", nullable=False, server_default="customer_order")

def downgrade() -> None:
    op.drop_constraint("fk_orders_advisor_id_users", "orders", type_="foreignkey")
    op.drop_index("ix_orders_confirmation_mode", table_name="orders")
    op.drop_index("ix_orders_advisor_id", table_name="orders")
    op.drop_index("ix_orders_order_source", table_name="orders")
    op.drop_column("orders", "confirmation_mode")
    op.drop_column("orders", "advisor_id")
    op.drop_column("orders", "order_source")

