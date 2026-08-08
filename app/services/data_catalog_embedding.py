from __future__ import annotations

from dataclasses import dataclass

from app.repositories.data_catalog import DataCatalogRepository


@dataclass(frozen=True)
class CatalogEmbeddingResult:
    tables: int
    columns: int
    examples: int
    updated: int
    failed: int


def table_embedding_text(table) -> str:
    return "\n".join(
        [
            f"业务域：{table.domain.name}（{table.domain.code}）",
            f"表名：{table.table_name}",
            f"显示名称：{table.display_name}",
            f"别名：{'、'.join(table.aliases_json or [])}",
            f"表说明：{table.description}",
            f"业务用途：{table.business_purpose}",
            f"数据粒度：{table.grain_description}",
            f"推荐使用：{table.usage_guidance}",
            f"不应使用：{table.non_usage_guidance}",
        ]
    )


def column_embedding_text(column) -> str:
    table = column.table
    domain = table.domain
    return "\n".join(
        [
            f"业务域：{domain.name}（{domain.code}）",
            f"表：{table.display_name}（{table.table_name}）",
            f"字段：{column.display_name}（{column.column_name}）",
            f"别名：{'、'.join(column.aliases_json or [])}",
            f"字段说明：{column.description}",
            f"业务含义：{column.business_meaning}",
            f"语义类型：{column.semantic_type}",
            f"单位：{column.unit or '无'}",
        ]
    )


def example_embedding_text(example) -> str:
    return "\n".join(
        [
            f"业务域：{example.domain.name}（{example.domain.code}）",
            f"用户问题：{example.question}",
            f"标准问题：{example.normalized_question}",
            f"意图：{example.intent_code}",
            f"目标表：{'、'.join(example.target_table_names_json or [])}",
            f"目标字段：{'、'.join(example.target_column_names_json or [])}",
            f"解释：{example.explanation}",
        ]
    )


class DataCatalogEmbeddingService:
    def __init__(self, repository: DataCatalogRepository, embedding_provider) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider

    async def embed_pending(self, *, limit: int = 100) -> CatalogEmbeddingResult:
        tables, columns, examples = await self.repository.catalog_embedding_rows(limit=limit)
        records = [("table", item.id, table_embedding_text(item)) for item in tables]
        records.extend(("column", item.id, column_embedding_text(item)) for item in columns)
        records.extend(("example", item.id, example_embedding_text(item)) for item in examples)
        if not records:
            return CatalogEmbeddingResult(tables=0, columns=0, examples=0, updated=0, failed=0)

        table_vectors: dict[str, list[float]] = {}
        column_vectors: dict[str, list[float]] = {}
        example_vectors: dict[str, list[float]] = {}
        failed = 0
        for kind, identifier, text in records:
            try:
                vector = (await self.embedding_provider.embed([text]))[0]
                if kind == "table":
                    table_vectors[identifier] = vector
                elif kind == "column":
                    column_vectors[identifier] = vector
                else:
                    example_vectors[identifier] = vector
                await self.repository.save_embeddings(tables=table_vectors, columns=column_vectors, examples=example_vectors)
                table_vectors.clear()
                column_vectors.clear()
                example_vectors.clear()
            except Exception:
                failed += 1
        updated = len(records) - failed
        return CatalogEmbeddingResult(tables=len(tables), columns=len(columns), examples=len(examples), updated=updated, failed=failed)
