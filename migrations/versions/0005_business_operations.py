"""add business operation records"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision="0005_business_operations"
down_revision="0004_order_account_constraints"
branch_labels=None
depends_on=None
def upgrade():
 op.execute("""CREATE TABLE IF NOT EXISTS business_operation (id VARCHAR(36) PRIMARY KEY,user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,operation_type VARCHAR(32) NOT NULL,status VARCHAR(32) NOT NULL DEFAULT 'accepted',idempotency_key VARCHAR(128),amount NUMERIC(18,2) NOT NULL DEFAULT 0,quantity NUMERIC(18,4) NOT NULL DEFAULT 0,product_id VARCHAR(36) REFERENCES product(id) ON DELETE SET NULL,to_account VARCHAR(128),payload_json TEXT NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ DEFAULT now(),updated_at TIMESTAMPTZ DEFAULT now())""")
 op.execute("CREATE INDEX IF NOT EXISTS ix_business_operation_user_id ON business_operation(user_id)")
 op.execute("CREATE INDEX IF NOT EXISTS ix_business_operation_operation_type ON business_operation(operation_type)")
 op.execute("CREATE INDEX IF NOT EXISTS ix_business_operation_idempotency_key ON business_operation(idempotency_key)")
 op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_business_operation_user_idempotency ON business_operation(user_id,idempotency_key) WHERE idempotency_key IS NOT NULL")
 return
 op.create_table("business_operation",sa.Column("id",sa.String(36),primary_key=True),sa.Column("user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),sa.Column("operation_type",sa.String(32),nullable=False),sa.Column("status",sa.String(32),nullable=False,server_default="accepted"),sa.Column("idempotency_key",sa.String(128),nullable=True),sa.Column("amount",sa.Numeric(18,2),nullable=False,server_default="0"),sa.Column("quantity",sa.Numeric(18,4),nullable=False,server_default="0"),sa.Column("product_id",sa.String(36),sa.ForeignKey("product.id",ondelete="SET NULL"),nullable=True),sa.Column("to_account",sa.String(128),nullable=True),sa.Column("payload_json",sa.Text(),nullable=False,server_default="{}"),sa.Column("created_at",sa.DateTime(timezone=True),server_default=sa.func.now()),sa.Column("updated_at",sa.DateTime(timezone=True),server_default=sa.func.now()))
 op.create_index("ix_business_operation_user_id","business_operation",["user_id"]); op.create_index("ix_business_operation_operation_type","business_operation",["operation_type"]); op.create_index("ix_business_operation_idempotency_key","business_operation",["idempotency_key"]); op.create_unique_constraint("uq_business_operation_user_idempotency","business_operation",["user_id","idempotency_key"])
def downgrade():
 op.drop_constraint("uq_business_operation_user_idempotency","business_operation",type_="unique"); op.drop_index("ix_business_operation_idempotency_key",table_name="business_operation"); op.drop_index("ix_business_operation_operation_type",table_name="business_operation"); op.drop_index("ix_business_operation_user_id",table_name="business_operation"); op.drop_table("business_operation")
