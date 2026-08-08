from typing import Any

from pydantic import BaseModel, Field


class DataCatalogSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    domain_code: str | None = Field(default=None, max_length=64)


class DataCatalogColumnResponse(BaseModel):
    id: str
    column_name: str
    display_name: str
    description: str
    business_meaning: str
    semantic_type: str
    unit: str
    is_sensitive: bool
    is_queryable: bool
    allowed_aggregations: list[str]
    aliases: list[str]
    example_values: list[str] = Field(default_factory=list)


class DataCatalogTableResponse(BaseModel):
    id: str
    domain_code: str
    domain_name: str
    table_name: str
    display_name: str
    description: str
    business_purpose: str
    grain_description: str
    usage_guidance: str
    non_usage_guidance: str
    table_kind: str
    permission_level: str
    allowed_roles: list[str]
    aliases: list[str]
    is_queryable: bool
    is_active: bool
    columns: list[DataCatalogColumnResponse]


class DataCatalogExampleResponse(BaseModel):
    question: str
    intent_code: str
    target_tables: list[str]
    target_columns: list[str]
    sql_template: str
    explanation: str


class DataCatalogRelationResponse(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str
    description: str
    join_guidance: str


class DataCatalogSearchResponse(BaseModel):
    query: str
    domain_code: str | None
    role_codes: list[str]
    retrieval_mode: str
    tables: list[DataCatalogTableResponse]
    examples: list[DataCatalogExampleResponse]
    relations: list[DataCatalogRelationResponse]
    nl2sql_ready: bool
    next_guard: str


class DataCatalogTableListResponse(BaseModel):
    total: int
    items: list[DataCatalogTableResponse]


class DataCatalogTableUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    business_purpose: str | None = Field(default=None, max_length=8000)
    grain_description: str | None = Field(default=None, max_length=8000)
    usage_guidance: str | None = Field(default=None, max_length=8000)
    non_usage_guidance: str | None = Field(default=None, max_length=8000)
    table_kind: str | None = Field(default=None, max_length=32)
    permission_level: str | None = Field(default=None, max_length=32)
    allowed_roles: list[str] | None = None
    aliases: list[str] | None = None
    is_queryable: bool | None = None
    is_active: bool | None = None
    change_note: str = Field(default="", max_length=1000)


class DataCatalogColumnUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    business_meaning: str | None = Field(default=None, max_length=8000)
    semantic_type: str | None = Field(default=None, max_length=64)
    unit: str | None = Field(default=None, max_length=32)
    is_sensitive: bool | None = None
    is_queryable: bool | None = None
    allowed_aggregations: list[str] | None = None
    aliases: list[str] | None = None
    example_values: list[str] | None = None
    change_note: str = Field(default="", max_length=1000)


class DataCatalogRevisionResponse(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    revision_no: int
    action: str
    snapshot: dict[str, Any]
    changed_by: str | None
    change_note: str
    created_at: Any
