"""add safe order/account integrity constraints"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = "0004_order_account_constraints"
down_revision: Union[str, None] = "0003_order_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade() -> None:
    op.create_check_constraint("ck_account_available_non_negative", "account", sa.text("available_balance >= 0"))
    op.create_check_constraint("ck_account_frozen_non_negative", "account", sa.text("frozen_balance >= 0"))
    op.create_unique_constraint("uq_trade_order_id", "trades", ["order_id"])
    op.create_unique_constraint("uq_money_flow_order_cash_out", "money_flow", ["order_id", "flow_type", "direction"])
def downgrade() -> None:
    op.drop_constraint("uq_money_flow_order_cash_out", "money_flow", type_="unique")
    op.drop_constraint("uq_trade_order_id", "trades", type_="unique")
    op.drop_constraint("ck_account_frozen_non_negative", "account", type_="check")
    op.drop_constraint("ck_account_available_non_negative", "account", type_="check")
