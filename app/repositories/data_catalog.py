from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from uuid import uuid4

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import selectinload

from app.db.session import Database
from app.models.data_catalog import (
    DataCatalogColumn,
    DataCatalogDomain,
    DataCatalogExampleQuery,
    DataCatalogRelation,
    DataCatalogRevision,
    DataCatalogTable,
)


def _query_terms(query: str) -> list[str]:
    normalized = query.strip().replace("，", " ").replace("。", " ").replace("？", " ")
    terms = [term for term in normalized.split() if term]
    if len(terms) == 1 and len(terms[0]) > 2:
        terms.extend(terms[0][index:index + 2] for index in range(len(terms[0]) - 1))
    return list(dict.fromkeys(terms)) or [query.strip()]


@dataclass(frozen=True)
class CatalogTableUpdate:
    display_name: str | None = None
    description: str | None = None
    business_purpose: str | None = None
    grain_description: str | None = None
    usage_guidance: str | None = None
    non_usage_guidance: str | None = None
    table_kind: str | None = None
    permission_level: str | None = None
    allowed_roles_json: list[str] | None = None
    aliases_json: list[str] | None = None
    is_queryable: bool | None = None
    is_active: bool | None = None
    change_note: str = ""


@dataclass(frozen=True)
class CatalogColumnUpdate:
    display_name: str | None = None
    description: str | None = None
    business_meaning: str | None = None
    semantic_type: str | None = None
    unit: str | None = None
    is_sensitive: bool | None = None
    is_queryable: bool | None = None
    allowed_aggregations_json: list[str] | None = None
    aliases_json: list[str] | None = None
    example_values_json: list[str] | None = None
    change_note: str = ""


class DataCatalogRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _role_clause(column, role_codes: Iterable[str]):
        roles = [role for role in role_codes if role]
        if not roles:
            return column == []
        return column.contains(roles[:1]) if len(roles) == 1 else or_(*[column.contains([role]) for role in roles])

    async def enqueue_embedding(self, *, limit: int = 100) -> str:
        from app.models.outbox import OutboxEvent

        async with self.database.session_factory() as session:
            event = OutboxEvent(
                id=str(uuid4()),
                event_type="data_catalog.embedding_requested",
                aggregate_type="data_catalog",
                aggregate_id=None,
                payload_json={"limit": limit},
                status="pending",
            )
            session.add(event)
            await session.commit()
            return str(event.id)
    async def list_tables(self, *, domain_code: str | None = None, limit: int = 100, offset: int = 0) -> tuple[int, list[DataCatalogTable]]:
        statement = select(DataCatalogTable).join(DataCatalogDomain).options(
            selectinload(DataCatalogTable.domain), selectinload(DataCatalogTable.columns)
        )
        count_statement = select(func.count()).select_from(DataCatalogTable).join(DataCatalogDomain)
        if domain_code:
            statement = statement.where(DataCatalogDomain.code == domain_code)
            count_statement = count_statement.where(DataCatalogDomain.code == domain_code)
        statement = statement.order_by(DataCatalogDomain.code, DataCatalogTable.table_name).limit(limit).offset(offset)
        async with self.database.session_factory() as session:
            total = int((await session.execute(count_statement)).scalar_one())
            rows = list((await session.execute(statement)).scalars().unique().all())
            return total, rows

    async def get_table(self, table_id: str) -> DataCatalogTable | None:
        async with self.database.session_factory() as session:
            return (
                await session.execute(
                    select(DataCatalogTable)
                    .options(selectinload(DataCatalogTable.domain), selectinload(DataCatalogTable.columns))
                    .where(DataCatalogTable.id == table_id)
                )
            ).scalar_one_or_none()

    async def update_table(self, *, table_id: str, payload: CatalogTableUpdate, actor_user_id: str) -> DataCatalogTable | None:
        values = {key: value for key, value in asdict(payload).items() if key != "change_note" and value is not None}
        async with self.database.session_factory() as session:
            table = (
                await session.execute(
                    select(DataCatalogTable)
                    .options(selectinload(DataCatalogTable.domain), selectinload(DataCatalogTable.columns))
                    .where(DataCatalogTable.id == table_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if table is None:
                return None
            for key, value in values.items():
                setattr(table, key, value)
            await session.flush()
            await self._append_revision(session, resource_type="table", resource_id=table.id, snapshot=self.table_snapshot(table), actor_user_id=actor_user_id, action="updated", note=payload.change_note)
            await session.commit()
            await session.refresh(table)
            return table

    async def get_column(self, column_id: str) -> DataCatalogColumn | None:
        async with self.database.session_factory() as session:
            return (
                await session.execute(
                    select(DataCatalogColumn)
                    .options(selectinload(DataCatalogColumn.table).selectinload(DataCatalogTable.domain))
                    .where(DataCatalogColumn.id == column_id)
                )
            ).scalar_one_or_none()

    async def update_column(self, *, column_id: str, payload: CatalogColumnUpdate, actor_user_id: str) -> DataCatalogColumn | None:
        values = {key: value for key, value in asdict(payload).items() if key != "change_note" and value is not None}
        async with self.database.session_factory() as session:
            column = (
                await session.execute(
                    select(DataCatalogColumn)
                    .options(selectinload(DataCatalogColumn.table).selectinload(DataCatalogTable.domain))
                    .where(DataCatalogColumn.id == column_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if column is None:
                return None
            for key, value in values.items():
                setattr(column, key, value)
            await session.flush()
            await self._append_revision(session, resource_type="column", resource_id=column.id, snapshot=self.column_snapshot(column), actor_user_id=actor_user_id, action="updated", note=payload.change_note)
            await session.commit()
            await session.refresh(column)
            return column

    async def list_revisions(self, *, resource_type: str, resource_id: str, limit: int = 30) -> list[DataCatalogRevision]:
        async with self.database.session_factory() as session:
            return list(
                (
                    await session.execute(
                        select(DataCatalogRevision)
                        .where(DataCatalogRevision.resource_type == resource_type, DataCatalogRevision.resource_id == resource_id)
                        .order_by(DataCatalogRevision.revision_no.desc())
                        .limit(limit)
                    )
                ).scalars().all()
            )

    async def _append_revision(self, session, *, resource_type: str, resource_id: str, snapshot: dict, actor_user_id: str, action: str, note: str) -> None:
        latest = await session.scalar(
            select(func.max(DataCatalogRevision.revision_no)).where(
                DataCatalogRevision.resource_type == resource_type,
                DataCatalogRevision.resource_id == resource_id,
            )
        )
        session.add(
            DataCatalogRevision(
                id=str(uuid4()),
                resource_type=resource_type,
                resource_id=resource_id,
                revision_no=int(latest or 0) + 1,
                action=action,
                snapshot_json=snapshot,
                changed_by=actor_user_id,
                change_note=note.strip(),
            )
        )

    @staticmethod
    def table_snapshot(table: DataCatalogTable) -> dict:
        return {
            "id": table.id,
            "domain_code": table.domain.code if table.domain else "",
            "table_name": table.table_name,
            "display_name": table.display_name,
            "description": table.description,
            "business_purpose": table.business_purpose,
            "grain_description": table.grain_description,
            "usage_guidance": table.usage_guidance,
            "non_usage_guidance": table.non_usage_guidance,
            "table_kind": table.table_kind,
            "permission_level": table.permission_level,
            "allowed_roles_json": table.allowed_roles_json,
            "aliases_json": table.aliases_json,
            "is_queryable": table.is_queryable,
            "is_active": table.is_active,
        }

    @staticmethod
    def column_snapshot(column: DataCatalogColumn) -> dict:
        return {
            "id": column.id,
            "table_name": column.table.table_name if column.table else "",
            "column_name": column.column_name,
            "display_name": column.display_name,
            "description": column.description,
            "business_meaning": column.business_meaning,
            "semantic_type": column.semantic_type,
            "unit": column.unit,
            "is_sensitive": column.is_sensitive,
            "is_queryable": column.is_queryable,
            "allowed_aggregations_json": column.allowed_aggregations_json,
            "aliases_json": column.aliases_json,
            "example_values_json": column.example_values_json,
        }

    async def catalog_embedding_rows(self, *, limit: int = 200) -> tuple[list[DataCatalogTable], list[DataCatalogColumn], list[DataCatalogExampleQuery]]:
        async with self.database.session_factory() as session:
            tables = list(
                (
                    await session.execute(
                        select(DataCatalogTable)
                        .options(selectinload(DataCatalogTable.domain))
                        .where(DataCatalogTable.is_active.is_(True), DataCatalogTable.embedding.is_(None))
                        .order_by(DataCatalogTable.updated_at)
                        .limit(limit)
                    )
                ).scalars().all()
            )
            remaining = max(0, limit - len(tables))
            columns = list(
                (
                    await session.execute(
                        select(DataCatalogColumn)
                        .options(selectinload(DataCatalogColumn.table).selectinload(DataCatalogTable.domain))
                        .where(DataCatalogColumn.is_queryable.is_(True), DataCatalogColumn.embedding.is_(None))
                        .order_by(DataCatalogColumn.updated_at)
                        .limit(remaining)
                    )
                ).scalars().all()
            )
            remaining = max(0, remaining - len(columns))
            examples = list(
                (
                    await session.execute(
                        select(DataCatalogExampleQuery)
                        .options(selectinload(DataCatalogExampleQuery.domain))
                        .where(DataCatalogExampleQuery.is_active.is_(True), DataCatalogExampleQuery.embedding.is_(None))
                        .order_by(DataCatalogExampleQuery.updated_at)
                        .limit(remaining)
                    )
                ).scalars().all()
            )
            return tables, columns, examples

    async def save_embeddings(self, *, tables: dict[str, list[float]], columns: dict[str, list[float]], examples: dict[str, list[float]]) -> int:
        updated = 0
        async with self.database.session_factory() as session:
            for model, values in ((DataCatalogTable, tables), (DataCatalogColumn, columns), (DataCatalogExampleQuery, examples)):
                for identifier, vector in values.items():
                    row = await session.get(model, identifier)
                    if row is not None:
                        row.embedding = vector
                        updated += 1
            await session.commit()
        return updated

    async def has_embeddings(self, *, role_codes: Iterable[str], domain_code: str | None = None) -> bool:
        statement = (
            select(DataCatalogTable.id)
            .join(DataCatalogDomain)
            .where(
                DataCatalogTable.is_active.is_(True),
                DataCatalogTable.is_queryable.is_(True),
                DataCatalogTable.embedding.is_not(None),
                DataCatalogDomain.is_active.is_(True),
                self._role_clause(DataCatalogTable.allowed_roles_json, role_codes),
            )
            .limit(1)
        )
        if domain_code:
            statement = statement.where(DataCatalogDomain.code == domain_code)
        async with self.database.session_factory() as session:
            return (await session.execute(statement)).scalar_one_or_none() is not None
    async def search_tables_vector(self, *, query_embedding: list[float], role_codes: Iterable[str], top_k: int, domain_code: str | None = None) -> list[tuple[DataCatalogTable, float]]:
        distance = DataCatalogTable.embedding.cosine_distance(query_embedding)
        statement = (
            select(DataCatalogTable, distance.label("distance"))
            .join(DataCatalogDomain)
            .options(selectinload(DataCatalogTable.domain), selectinload(DataCatalogTable.columns))
            .where(
                DataCatalogTable.is_active.is_(True),
                DataCatalogTable.is_queryable.is_(True),
                DataCatalogTable.embedding.is_not(None),
                DataCatalogDomain.is_active.is_(True),
                self._role_clause(DataCatalogTable.allowed_roles_json, role_codes),
            )
            .order_by(distance)
            .limit(top_k)
        )
        if domain_code:
            statement = statement.where(DataCatalogDomain.code == domain_code)
        async with self.database.session_factory() as session:
            return [(row, max(0.0, 1.0 - float(distance_value))) for row, distance_value in (await session.execute(statement)).all()]

    async def search_columns_vector(self, *, query_embedding: list[float], role_codes: Iterable[str], table_ids: Iterable[str], top_k: int) -> list[tuple[DataCatalogColumn, float]]:
        identifiers = list(table_ids)
        if not identifiers:
            return []
        distance = DataCatalogColumn.embedding.cosine_distance(query_embedding)
        statement = (
            select(DataCatalogColumn, distance.label("distance"))
            .join(DataCatalogTable)
            .options(selectinload(DataCatalogColumn.table).selectinload(DataCatalogTable.domain))
            .where(
                DataCatalogTable.is_active.is_(True),
                DataCatalogTable.is_queryable.is_(True),
                DataCatalogColumn.is_queryable.is_(True),
                DataCatalogColumn.embedding.is_not(None),
                DataCatalogColumn.table_id.in_(identifiers),
                self._role_clause(DataCatalogTable.allowed_roles_json, role_codes),
            )
            .order_by(distance)
            .limit(top_k)
        )
        async with self.database.session_factory() as session:
            return [(row, max(0.0, 1.0 - float(distance_value))) for row, distance_value in (await session.execute(statement)).all()]

    async def search_examples_vector(self, *, query_embedding: list[float], role_codes: Iterable[str], top_k: int, domain_code: str | None = None) -> list[tuple[DataCatalogExampleQuery, float]]:
        distance = DataCatalogExampleQuery.embedding.cosine_distance(query_embedding)
        statement = (
            select(DataCatalogExampleQuery, distance.label("distance"))
            .join(DataCatalogDomain)
            .where(
                DataCatalogExampleQuery.is_active.is_(True),
                DataCatalogExampleQuery.is_approved.is_(True),
                DataCatalogExampleQuery.embedding.is_not(None),
                DataCatalogDomain.is_active.is_(True),
                self._role_clause(DataCatalogExampleQuery.allowed_roles_json, role_codes),
            )
            .order_by(distance)
            .limit(top_k)
        )
        if domain_code:
            statement = statement.where(DataCatalogDomain.code == domain_code)
        async with self.database.session_factory() as session:
            return [(row, max(0.0, 1.0 - float(distance_value))) for row, distance_value in (await session.execute(statement)).all()]
    async def search_tables(
        self,
        *,
        query: str,
        role_codes: Iterable[str],
        top_k: int = 5,
        domain_code: str | None = None,
    ) -> list[DataCatalogTable]:
        text_clauses = []
        for term in _query_terms(query):
            normalized = f"%{term}%"
            text_clauses.append(
                or_(
                    DataCatalogTable.table_name.ilike(normalized),
                    DataCatalogTable.display_name.ilike(normalized),
                    DataCatalogTable.description.ilike(normalized),
                    DataCatalogTable.business_purpose.ilike(normalized),
                    DataCatalogTable.grain_description.ilike(normalized),
                    DataCatalogTable.usage_guidance.ilike(normalized),
                    DataCatalogTable.aliases_json.contains([term]),
                )
            )
        statement: Select = (
            select(DataCatalogTable)
            .join(DataCatalogDomain)
            .options(selectinload(DataCatalogTable.domain), selectinload(DataCatalogTable.columns))
            .where(
                DataCatalogTable.is_active.is_(True),
                DataCatalogTable.is_queryable.is_(True),
                DataCatalogDomain.is_active.is_(True),
                self._role_clause(DataCatalogTable.allowed_roles_json, role_codes),
                or_(*text_clauses),
            )
            .order_by(DataCatalogTable.display_name)
            .limit(top_k)
        )
        if domain_code:
            statement = statement.where(DataCatalogDomain.code == domain_code)
        async with self.database.session_factory() as session:
            return list((await session.execute(statement)).scalars().unique().all())

    async def search_columns(
        self,
        *,
        query: str,
        role_codes: Iterable[str],
        top_k: int = 8,
        table_ids: Iterable[str] | None = None,
    ) -> list[DataCatalogColumn]:
        text_clauses = []
        for term in _query_terms(query):
            normalized = f"%{term}%"
            text_clauses.append(
                or_(
                    DataCatalogColumn.column_name.ilike(normalized),
                    DataCatalogColumn.display_name.ilike(normalized),
                    DataCatalogColumn.description.ilike(normalized),
                    DataCatalogColumn.business_meaning.ilike(normalized),
                    DataCatalogColumn.aliases_json.contains([term]),
                )
            )
        statement: Select = (
            select(DataCatalogColumn)
            .join(DataCatalogTable)
            .options(selectinload(DataCatalogColumn.table).selectinload(DataCatalogTable.domain))
            .where(
                DataCatalogTable.is_active.is_(True),
                DataCatalogTable.is_queryable.is_(True),
                DataCatalogColumn.is_queryable.is_(True),
                self._role_clause(DataCatalogTable.allowed_roles_json, role_codes),
                or_(*text_clauses),
            )
            .order_by(DataCatalogColumn.display_name)
            .limit(top_k)
        )
        identifiers = list(table_ids or [])
        if identifiers:
            statement = statement.where(DataCatalogColumn.table_id.in_(identifiers))
        async with self.database.session_factory() as session:
            return list((await session.execute(statement)).scalars().unique().all())

    async def search_examples(
        self,
        *,
        query: str,
        role_codes: Iterable[str],
        top_k: int = 5,
        domain_code: str | None = None,
    ) -> list[DataCatalogExampleQuery]:
        text_clauses = []
        for term in _query_terms(query):
            normalized = f"%{term}%"
            text_clauses.append(
                or_(
                    DataCatalogExampleQuery.question.ilike(normalized),
                    DataCatalogExampleQuery.normalized_question.ilike(normalized),
                    DataCatalogExampleQuery.explanation.ilike(normalized),
                    DataCatalogExampleQuery.intent_code.ilike(normalized),
                )
            )
        statement: Select = (
            select(DataCatalogExampleQuery)
            .join(DataCatalogDomain)
            .where(
                DataCatalogExampleQuery.is_active.is_(True),
                DataCatalogExampleQuery.is_approved.is_(True),
                DataCatalogDomain.is_active.is_(True),
                self._role_clause(DataCatalogExampleQuery.allowed_roles_json, role_codes),
                or_(*text_clauses),
            )
            .order_by(DataCatalogExampleQuery.intent_code)
            .limit(top_k)
        )
        if domain_code:
            statement = statement.where(DataCatalogDomain.code == domain_code)
        async with self.database.session_factory() as session:
            return list((await session.execute(statement)).scalars().all())

    async def relations_for_tables(self, *, table_ids: Iterable[str], role_codes: Iterable[str]) -> list[DataCatalogRelation]:
        identifiers = list(table_ids)
        if not identifiers:
            return []
        statement = (
            select(DataCatalogRelation)
            .join(DataCatalogTable, DataCatalogRelation.from_table_id == DataCatalogTable.id)
            .where(
                DataCatalogRelation.is_active.is_(True),
                DataCatalogRelation.from_table_id.in_(identifiers),
                self._role_clause(DataCatalogTable.allowed_roles_json, role_codes),
            )
            .order_by(DataCatalogRelation.from_table_id, DataCatalogRelation.from_column_name)
        )
        async with self.database.session_factory() as session:
            return list((await session.execute(statement)).scalars().all())
