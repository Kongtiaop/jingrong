"""add deterministic product daily yield rate"""
from alembic import op
import sqlalchemy as sa
revision = "0007_product_yield"
down_revision = "0006_staff_operation_fields"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("product", sa.Column("daily_yield_rate", sa.Numeric(12, 8), nullable=False, server_default="0"))
def downgrade():
    op.drop_column("product", "daily_yield_rate")
