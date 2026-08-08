"""add AML late-rescan lineage"""
from alembic import op
import sqlalchemy as sa

revision = "0016_aml_late_rescan"
down_revision = "0015_aml_scheduler_state"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("aml_batch_run", sa.Column("run_type", sa.String(32), nullable=False, server_default="daily"))
    op.add_column("aml_batch_run", sa.Column("source_batch_id", sa.String(36), nullable=True))
    op.create_index("ix_aml_batch_run_source_batch_id", "aml_batch_run", ["source_batch_id"])

def downgrade():
    op.drop_index("ix_aml_batch_run_source_batch_id", table_name="aml_batch_run")
    op.drop_column("aml_batch_run", "source_batch_id")
    op.drop_column("aml_batch_run", "run_type")
