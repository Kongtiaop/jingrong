from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.repositories.data_catalog import DataCatalogRepository


@dataclass(frozen=True)
class CatalogSearchScope:
    role_codes: tuple[str, ...]

    @classmethod
    def from_user(cls, user) -> "CatalogSearchScope":
        roles = {role.code for role in user.roles}
        if user.is_super_admin:
            roles.add("super_admin")
        return cls(role_codes=tuple(sorted(roles)))


class DataCatalogService:
    def __init__(self, repository: DataCatalogRepository, embedding_provider=None) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider

    async def search(self, *, query: str, scope: CatalogSearchScope, top_k: int = 5, domain_code: str | None = None) -> dict:
        text_tables = await self.repository.search_tables(query=query, role_codes=scope.role_codes, top_k=top_k, domain_code=domain_code)
        text_examples = await self.repository.search_examples(query=query, role_codes=scope.role_codes, top_k=top_k, domain_code=domain_code)
        table_scores: dict[str, float] = {item.id: 0.55 for item in text_tables}
        table_map = {item.id: item for item in text_tables}
        example_map = {item.id: item for item in text_examples}
        example_scores = {item.id: 0.55 for item in text_examples}
        retrieval_mode = "catalog-text-first"
        query_embedding: list[float] | None = None

        if self.embedding_provider is not None and await self.repository.has_embeddings(role_codes=scope.role_codes, domain_code=domain_code):
            try:
                query_embedding = (await asyncio.wait_for(self.embedding_provider.embed([query]), timeout=8.0))[0]
                vector_tables = await self.repository.search_tables_vector(
                    query_embedding=query_embedding,
                    role_codes=scope.role_codes,
                    top_k=top_k,
                    domain_code=domain_code,
                )
                for table, score in vector_tables:
                    table_map[table.id] = table
                    table_scores[table.id] = max(table_scores.get(table.id, 0.0), 0.45 + score * 0.55)
                vector_examples = await self.repository.search_examples_vector(
                    query_embedding=query_embedding,
                    role_codes=scope.role_codes,
                    top_k=top_k,
                    domain_code=domain_code,
                )
                for example, score in vector_examples:
                    example_map[example.id] = example
                    example_scores[example.id] = max(example_scores.get(example.id, 0.0), 0.45 + score * 0.55)
                retrieval_mode = "catalog-hybrid-text-pgvector"
            except Exception:
                retrieval_mode = "catalog-text-fallback"

        tables = [
            item
            for item, _ in sorted(
                ((table_map[key], score) for key, score in table_scores.items()),
                key=lambda pair: (-pair[1], pair[0].display_name),
            )[:top_k]
        ]
        table_ids = [item.id for item in tables]
        text_columns = await self.repository.search_columns(query=query, role_codes=scope.role_codes, top_k=max(top_k * 2, 6), table_ids=table_ids)
        column_scores: dict[str, float] = {item.id: 0.55 for item in text_columns}
        column_map = {item.id: item for item in text_columns}
        if query_embedding is not None and table_ids and retrieval_mode == "catalog-hybrid-text-pgvector":
            try:
                vector_columns = await self.repository.search_columns_vector(
                    query_embedding=query_embedding,
                    role_codes=scope.role_codes,
                    table_ids=table_ids,
                    top_k=max(top_k * 2, 6),
                )
                for column, score in vector_columns:
                    column_map[column.id] = column
                    column_scores[column.id] = max(column_scores.get(column.id, 0.0), 0.45 + score * 0.55)
            except Exception:
                pass
        columns = [
            item
            for item, _ in sorted(
                ((column_map[key], score) for key, score in column_scores.items()),
                key=lambda pair: (-pair[1], pair[0].display_name),
            )
        ]
        examples = [
            item
            for item, _ in sorted(
                ((example_map[key], score) for key, score in example_scores.items()),
                key=lambda pair: (-pair[1], pair[0].intent_code),
            )[:top_k]
        ]
        relations = await self.repository.relations_for_tables(table_ids=table_ids, role_codes=scope.role_codes)

        relation_lookup = {item.id: item for item in tables}
        table_payload = []
        for table in tables:
            matched_columns = [column for column in columns if column.table_id == table.id]
            table_payload.append(
                {
                    "id": table.id,
                    "domain_code": table.domain.code,
                    "domain_name": table.domain.name,
                    "table_name": table.table_name,
                    "display_name": table.display_name,
                    "description": table.description,
                    "business_purpose": table.business_purpose,
                    "grain_description": table.grain_description,
                    "usage_guidance": table.usage_guidance,
                    "non_usage_guidance": table.non_usage_guidance,
                    "table_kind": table.table_kind,
                    "permission_level": table.permission_level,
                    "allowed_roles": table.allowed_roles_json,
                    "aliases": table.aliases_json,
                    "is_queryable": table.is_queryable,
                    "is_active": table.is_active,
                    "columns": [
                        {
                            "id": column.id,
                            "column_name": column.column_name,
                            "display_name": column.display_name,
                            "description": column.description,
                            "business_meaning": column.business_meaning,
                            "semantic_type": column.semantic_type,
                            "unit": column.unit,
                            "is_sensitive": column.is_sensitive,
                            "is_queryable": column.is_queryable,
                            "allowed_aggregations": column.allowed_aggregations_json,
                            "aliases": column.aliases_json,
                            "example_values": column.example_values_json,
                        }
                        for column in matched_columns
                    ],
                }
            )

        relation_payload = []
        for relation in relations:
            from_table = relation_lookup.get(relation.from_table_id)
            to_table = relation_lookup.get(relation.to_table_id)
            if from_table is None or to_table is None:
                continue
            relation_payload.append(
                {
                    "from_table": from_table.table_name,
                    "from_column": relation.from_column_name,
                    "to_table": to_table.table_name,
                    "to_column": relation.to_column_name,
                    "cardinality": relation.cardinality,
                    "description": relation.description,
                    "join_guidance": relation.join_guidance,
                }
            )

        return {
            "query": query,
            "domain_code": domain_code,
            "role_codes": list(scope.role_codes),
            "retrieval_mode": retrieval_mode,
            "tables": table_payload,
            "examples": [
                {
                    "question": item.question,
                    "intent_code": item.intent_code,
                    "target_tables": item.target_table_names_json,
                    "target_columns": item.target_column_names_json,
                    "sql_template": item.sql_template,
                    "explanation": item.explanation,
                }
                for item in examples
            ],
            "relations": relation_payload,
            "nl2sql_ready": False,
            "next_guard": "Catalog retrieval only. SQL generation and execution remain disabled until AST validation, row-scope enforcement, read-only execution, and auditing are implemented.",
        }
