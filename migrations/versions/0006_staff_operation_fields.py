"""add staff operation fields"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision="0006_staff_operation_fields"
down_revision="0005_business_operations"
branch_labels=None
depends_on=None
def upgrade():
 op.add_column("business_operation",sa.Column("operator_user_id",sa.String(36),sa.ForeignKey("users.id",ondelete="SET NULL"),nullable=True)); op.add_column("business_operation",sa.Column("authorization_id",sa.String(36),sa.ForeignKey("customer_advisor_authorization.id",ondelete="SET NULL"),nullable=True)); op.add_column("business_operation",sa.Column("confirmation_mode",sa.String(32),nullable=False,server_default="customer")); op.create_index("ix_business_operation_operator_user_id","business_operation",["operator_user_id"]); op.create_index("ix_business_operation_authorization_id","business_operation",["authorization_id"])
def downgrade():
 op.drop_index("ix_business_operation_authorization_id",table_name="business_operation"); op.drop_index("ix_business_operation_operator_user_id",table_name="business_operation"); op.drop_column("business_operation","confirmation_mode"); op.drop_column("business_operation","authorization_id"); op.drop_column("business_operation","operator_user_id")
