from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import require_permission, require_staff_permission
from app.models.audit import AuditLog
from app.infrastructure.qwen import QwenProvider
from app.repositories.data_catalog import CatalogColumnUpdate, CatalogTableUpdate, DataCatalogRepository
from app.schemas.data_catalog import (
    DataCatalogColumnResponse,
    DataCatalogColumnUpdateRequest,
    DataCatalogRevisionResponse,
    DataCatalogSearchRequest,
    DataCatalogSearchResponse,
    DataCatalogTableListResponse,
    DataCatalogTableResponse,
    DataCatalogTableUpdateRequest,
)
from app.services.data_catalog_service import CatalogSearchScope, DataCatalogService
from uuid import uuid4


router = APIRouter(prefix="/data-catalog", tags=["data-catalog"])


def _table_response(table) -> DataCatalogTableResponse:
    return DataCatalogTableResponse(
        id=table.id,
        domain_code=table.domain.code,
        domain_name=table.domain.name,
        table_name=table.table_name,
        display_name=table.display_name,
        description=table.description,
        business_purpose=table.business_purpose,
        grain_description=table.grain_description,
        usage_guidance=table.usage_guidance,
        non_usage_guidance=table.non_usage_guidance,
        table_kind=table.table_kind,
        permission_level=table.permission_level,
        allowed_roles=table.allowed_roles_json,
        aliases=table.aliases_json,
        is_queryable=table.is_queryable,
        is_active=table.is_active,
        columns=[
            DataCatalogColumnResponse(
                id=column.id,
                column_name=column.column_name,
                display_name=column.display_name,
                description=column.description,
                business_meaning=column.business_meaning,
                semantic_type=column.semantic_type,
                unit=column.unit,
                is_sensitive=column.is_sensitive,
                is_queryable=column.is_queryable,
                allowed_aggregations=column.allowed_aggregations_json,
                aliases=column.aliases_json,
                example_values=column.example_values_json,
            )
            for column in sorted(table.columns, key=lambda item: item.ordinal_position)
        ],
    )


@router.post("/search", response_model=ApiResponse[DataCatalogSearchResponse])
async def search_data_catalog(
    request: Request,
    payload: DataCatalogSearchRequest,
    user=Depends(require_staff_permission("admin:write")),
) -> ApiResponse[DataCatalogSearchResponse]:
    provider = QwenProvider(request.app.state.settings)
    try:
        service = DataCatalogService(DataCatalogRepository(request.app.state.database), provider)
        data = await service.search(
            query=payload.query,
            scope=CatalogSearchScope.from_user(user),
            top_k=payload.top_k,
            domain_code=payload.domain_code,
        )
    finally:
        await provider.close()
    return ApiResponse(data=DataCatalogSearchResponse.model_validate(data), trace_id=get_trace_id())


@router.post("/embeddings", response_model=ApiResponse[dict], status_code=202)
async def enqueue_data_catalog_embeddings(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    user=Depends(require_permission("admin:write")),
) -> ApiResponse[dict]:
    event_id = await DataCatalogRepository(request.app.state.database).enqueue_embedding(limit=limit)
    async with request.app.state.database.session_factory() as session:
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="enqueue", resource_type="data_catalog_embedding", resource_id=event_id, detail=f"limit={limit}"))
        await session.commit()
    return ApiResponse(data={"event_id": event_id, "status": "queued", "limit": limit}, trace_id=get_trace_id())

@router.get("/tables", response_model=ApiResponse[DataCatalogTableListResponse])
async def list_data_catalog_tables(
    request: Request,
    domain_code: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(require_permission("admin:write")),
) -> ApiResponse[DataCatalogTableListResponse]:
    total, tables = await DataCatalogRepository(request.app.state.database).list_tables(domain_code=domain_code, limit=limit, offset=offset)
    return ApiResponse(data=DataCatalogTableListResponse(total=total, items=[_table_response(table) for table in tables]), trace_id=get_trace_id())


@router.put("/tables/{table_id}", response_model=ApiResponse[DataCatalogTableResponse])
async def update_data_catalog_table(
    request: Request,
    table_id: str,
    payload: DataCatalogTableUpdateRequest,
    user=Depends(require_permission("admin:write")),
) -> ApiResponse[DataCatalogTableResponse]:
    repository = DataCatalogRepository(request.app.state.database)
    table = await repository.update_table(
        table_id=table_id,
        actor_user_id=user.id,
        payload=CatalogTableUpdate(
            display_name=payload.display_name,
            description=payload.description,
            business_purpose=payload.business_purpose,
            grain_description=payload.grain_description,
            usage_guidance=payload.usage_guidance,
            non_usage_guidance=payload.non_usage_guidance,
            table_kind=payload.table_kind,
            permission_level=payload.permission_level,
            allowed_roles_json=payload.allowed_roles,
            aliases_json=payload.aliases,
            is_queryable=payload.is_queryable,
            is_active=payload.is_active,
            change_note=payload.change_note,
        ),
    )
    if table is None:
        raise HTTPException(status_code=404, detail={"code": "DATA_CATALOG_TABLE_NOT_FOUND", "message": "catalog table not found"})
    async with request.app.state.database.session_factory() as session:
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="update", resource_type="data_catalog_table", resource_id=table.id, detail=payload.change_note or table.table_name))
        await session.commit()
    return ApiResponse(data=_table_response(table), trace_id=get_trace_id())


@router.put("/columns/{column_id}", response_model=ApiResponse[DataCatalogColumnResponse])
async def update_data_catalog_column(
    request: Request,
    column_id: str,
    payload: DataCatalogColumnUpdateRequest,
    user=Depends(require_permission("admin:write")),
) -> ApiResponse[DataCatalogColumnResponse]:
    repository = DataCatalogRepository(request.app.state.database)
    column = await repository.update_column(
        column_id=column_id,
        actor_user_id=user.id,
        payload=CatalogColumnUpdate(
            display_name=payload.display_name,
            description=payload.description,
            business_meaning=payload.business_meaning,
            semantic_type=payload.semantic_type,
            unit=payload.unit,
            is_sensitive=payload.is_sensitive,
            is_queryable=payload.is_queryable,
            allowed_aggregations_json=payload.allowed_aggregations,
            aliases_json=payload.aliases,
            example_values_json=payload.example_values,
            change_note=payload.change_note,
        ),
    )
    if column is None:
        raise HTTPException(status_code=404, detail={"code": "DATA_CATALOG_COLUMN_NOT_FOUND", "message": "catalog column not found"})
    async with request.app.state.database.session_factory() as session:
        session.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="update", resource_type="data_catalog_column", resource_id=column.id, detail=payload.change_note or column.column_name))
        await session.commit()
    return ApiResponse(
        data=DataCatalogColumnResponse(
            id=column.id,
            column_name=column.column_name,
            display_name=column.display_name,
            description=column.description,
            business_meaning=column.business_meaning,
            semantic_type=column.semantic_type,
            unit=column.unit,
            is_sensitive=column.is_sensitive,
            is_queryable=column.is_queryable,
            allowed_aggregations=column.allowed_aggregations_json,
            aliases=column.aliases_json,
            example_values=column.example_values_json,
        ),
        trace_id=get_trace_id(),
    )


@router.get("/revisions/{resource_type}/{resource_id}", response_model=ApiResponse[list[DataCatalogRevisionResponse]])
async def list_data_catalog_revisions(
    request: Request,
    resource_type: str,
    resource_id: str,
    _user=Depends(require_permission("admin:write")),
) -> ApiResponse[list[DataCatalogRevisionResponse]]:
    if resource_type not in {"table", "column"}:
        raise HTTPException(status_code=400, detail={"code": "DATA_CATALOG_RESOURCE_TYPE_INVALID", "message": "resource_type must be table or column"})
    rows = await DataCatalogRepository(request.app.state.database).list_revisions(resource_type=resource_type, resource_id=resource_id)
    return ApiResponse(
        data=[
            DataCatalogRevisionResponse(
                id=row.id,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                revision_no=row.revision_no,
                action=row.action,
                snapshot=row.snapshot_json,
                changed_by=row.changed_by,
                change_note=row.change_note,
                created_at=row.created_at,
            )
            for row in rows
        ],
        trace_id=get_trace_id(),
    )
