"""preserve legacy customer profile investment horizon"""
from alembic import op
import sqlalchemy as sa
revision = "0008_customer_profile_horizon_compat"
down_revision = "0007_product_daily_yield_rate"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("customer_profile", sa.Column("investment_horizon", sa.String(64), nullable=False, server_default="3-5年"))
    op.execute("UPDATE customer_profile SET investment_horizon='3-5年' WHERE investment_horizon IS NULL")
def downgrade():
    op.drop_column("customer_profile", "investment_horizon")
