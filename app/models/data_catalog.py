from __future__ import annotations

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import VECTOR

from app.db.base import Base


class DataCatalogDomain(Base):
    __tablename__ = "data_catalog_domain"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    permission_level: Mapped[str] = mapped_column(String(32), nullable=False, default="internal", index=True)
    allowed_roles_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tables: Mapped[list["DataCatalogTable"]] = relationship(back_populates="domain")
    examples: Mapped[list["DataCatalogExampleQuery"]] = relationship(back_populates="domain")


class DataCatalogTable(Base):
    __tablename__ = "data_catalog_table"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("data_catalog_domain.id", ondelete="RESTRICT"), nullable=False, index=True)
    database_name: Mapped[str] = mapped_column(String(128), nullable=False, default="wealth_manager")
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False, default="public")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    business_purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    grain_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    usage_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    non_usage_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    table_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="entity", index=True)
    permission_level: Mapped[str] = mapped_column(String(32), nullable=False, default="internal", index=True)
    allowed_roles_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    aliases_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_queryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    domain: Mapped[DataCatalogDomain] = relationship(back_populates="tables")
    columns: Mapped[list["DataCatalogColumn"]] = relationship(back_populates="table", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("database_name", "schema_name", "table_name", name="uq_data_catalog_table_physical_name"),
    )


class DataCatalogRevision(Base):
    __tablename__ = "data_catalog_revision"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="updated")
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    changed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("resource_type", "resource_id", "revision_no", name="uq_data_catalog_revision_number"),
    )

class DataCatalogColumn(Base):
    __tablename__ = "data_catalog_column"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    table_id: Mapped[str] = mapped_column(ForeignKey("data_catalog_table.id", ondelete="CASCADE"), nullable=False, index=True)
    column_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    business_meaning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    semantic_type: Mapped[str] = mapped_column(String(64), nullable=False, default="text")
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_foreign_key: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_queryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    allowed_aggregations_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    aliases_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    example_values_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    table: Mapped[DataCatalogTable] = relationship(back_populates="columns")

    __table_args__ = (
        UniqueConstraint("table_id", "column_name", name="uq_data_catalog_column_name"),
    )


class DataCatalogRelation(Base):
    __tablename__ = "data_catalog_relation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    from_table_id: Mapped[str] = mapped_column(ForeignKey("data_catalog_table.id", ondelete="CASCADE"), nullable=False, index=True)
    from_column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    to_table_id: Mapped[str] = mapped_column(ForeignKey("data_catalog_table.id", ondelete="CASCADE"), nullable=False, index=True)
    to_column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="foreign_key")
    cardinality: Mapped[str] = mapped_column(String(32), nullable=False, default="many_to_one")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    join_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("from_table_id", "from_column_name", "to_table_id", "to_column_name", name="uq_data_catalog_relation_edge"),
    )


class DataCatalogExampleQuery(Base):
    __tablename__ = "data_catalog_example_query"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    domain_id: Mapped[str] = mapped_column(ForeignKey("data_catalog_domain.id", ondelete="CASCADE"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    intent_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_table_names_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    target_column_names_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    sql_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    allowed_roles_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    domain: Mapped[DataCatalogDomain] = relationship(back_populates="examples")
