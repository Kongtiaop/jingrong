"""create data catalog foundation for future NL2SQL retrieval"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0017_data_catalog"
down_revision = "0016_aml_late_rescan"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "data_catalog_domain",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("permission_level", sa.String(32), nullable=False, server_default="internal"),
        sa.Column("allowed_roles_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("code", name="uq_data_catalog_domain_code"),
    )
    op.create_index("ix_data_catalog_domain_code", "data_catalog_domain", ["code"])
    op.create_index("ix_data_catalog_domain_permission_level", "data_catalog_domain", ["permission_level"])
    op.create_index("ix_data_catalog_domain_is_active", "data_catalog_domain", ["is_active"])

    op.create_table(
        "data_catalog_table",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("domain_id", sa.String(36), sa.ForeignKey("data_catalog_domain.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("database_name", sa.String(128), nullable=False, server_default="wealth_manager"),
        sa.Column("schema_name", sa.String(128), nullable=False, server_default="public"),
        sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("business_purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("grain_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("usage_guidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("non_usage_guidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("table_kind", sa.String(32), nullable=False, server_default="entity"),
        sa.Column("permission_level", sa.String(32), nullable=False, server_default="internal"),
        sa.Column("allowed_roles_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("aliases_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("is_queryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("database_name", "schema_name", "table_name", name="uq_data_catalog_table_physical_name"),
    )
    op.create_index("ix_data_catalog_table_domain_id", "data_catalog_table", ["domain_id"])
    op.create_index("ix_data_catalog_table_table_name", "data_catalog_table", ["table_name"])
    op.create_index("ix_data_catalog_table_table_kind", "data_catalog_table", ["table_kind"])
    op.create_index("ix_data_catalog_table_permission_level", "data_catalog_table", ["permission_level"])
    op.create_index("ix_data_catalog_table_is_queryable", "data_catalog_table", ["is_queryable"])
    op.create_index("ix_data_catalog_table_is_active", "data_catalog_table", ["is_active"])

    op.create_table(
        "data_catalog_column",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("table_id", sa.String(36), sa.ForeignKey("data_catalog_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column("column_name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("business_meaning", sa.Text(), nullable=False, server_default=""),
        sa.Column("data_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("semantic_type", sa.String(64), nullable=False, server_default="text"),
        sa.Column("unit", sa.String(32), nullable=False, server_default=""),
        sa.Column("ordinal_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_nullable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_primary_key", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_foreign_key", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_queryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_aggregations_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("aliases_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("example_values_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("table_id", "column_name", name="uq_data_catalog_column_name"),
    )
    op.create_index("ix_data_catalog_column_table_id", "data_catalog_column", ["table_id"])
    op.create_index("ix_data_catalog_column_column_name", "data_catalog_column", ["column_name"])
    op.create_index("ix_data_catalog_column_is_sensitive", "data_catalog_column", ["is_sensitive"])
    op.create_index("ix_data_catalog_column_is_queryable", "data_catalog_column", ["is_queryable"])

    op.create_table(
        "data_catalog_relation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("from_table_id", sa.String(36), sa.ForeignKey("data_catalog_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_column_name", sa.String(128), nullable=False),
        sa.Column("to_table_id", sa.String(36), sa.ForeignKey("data_catalog_table.id", ondelete="CASCADE"), nullable=False),
        sa.Column("to_column_name", sa.String(128), nullable=False),
        sa.Column("relation_type", sa.String(32), nullable=False, server_default="foreign_key"),
        sa.Column("cardinality", sa.String(32), nullable=False, server_default="many_to_one"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("join_guidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("from_table_id", "from_column_name", "to_table_id", "to_column_name", name="uq_data_catalog_relation_edge"),
    )
    op.create_index("ix_data_catalog_relation_from_table_id", "data_catalog_relation", ["from_table_id"])
    op.create_index("ix_data_catalog_relation_to_table_id", "data_catalog_relation", ["to_table_id"])
    op.create_index("ix_data_catalog_relation_is_active", "data_catalog_relation", ["is_active"])

    op.create_table(
        "data_catalog_example_query",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("domain_id", sa.String(36), sa.ForeignKey("data_catalog_domain.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("normalized_question", sa.Text(), nullable=False),
        sa.Column("intent_code", sa.String(128), nullable=False),
        sa.Column("target_table_names_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("target_column_names_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("sql_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("allowed_roles_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_data_catalog_example_query_domain_id", "data_catalog_example_query", ["domain_id"])
    op.create_index("ix_data_catalog_example_query_intent_code", "data_catalog_example_query", ["intent_code"])
    op.create_index("ix_data_catalog_example_query_is_approved", "data_catalog_example_query", ["is_approved"])
    op.create_index("ix_data_catalog_example_query_is_active", "data_catalog_example_query", ["is_active"])

    op.execute("CREATE INDEX ix_data_catalog_table_embedding_hnsw ON data_catalog_table USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_data_catalog_column_embedding_hnsw ON data_catalog_column USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_data_catalog_example_query_embedding_hnsw ON data_catalog_example_query USING hnsw (embedding vector_cosine_ops)")


def downgrade():
    op.drop_table("data_catalog_example_query")
    op.drop_table("data_catalog_relation")
    op.drop_table("data_catalog_column")
    op.drop_table("data_catalog_table")
    op.drop_table("data_catalog_domain")
