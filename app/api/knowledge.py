import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.common.exceptions import ResourceNotFoundError
from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_permission
from app.repositories.knowledge import KnowledgeRepository
from app.infrastructure.qwen import QwenProvider
from app.schemas.knowledge import AdminKnowledgeUploadResponse, DocumentCreateRequest, DocumentIngestResponse, DocumentIngestionStatusResponse, DocumentResponse, KnowledgeBaseResponse, KnowledgeDocumentListResponse, KnowledgeSearchRequest, KnowledgeSearchResponse

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SUPPORTED_UPLOAD_SUFFIXES = {".txt", ".md", ".docx"}


@router.get("/documents", response_model=ApiResponse[KnowledgeDocumentListResponse])
async def list_documents(request: Request, limit: int = 100, offset: int = 0, _user=Depends(require_permission("product:write"))) -> ApiResponse[KnowledgeDocumentListResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    total, rows = await repository.list_documents(limit=min(max(limit, 1), 100), offset=max(offset, 0))
    return ApiResponse(
        data=KnowledgeDocumentListResponse(
            total=total,
            items=[DocumentResponse.model_validate(row, from_attributes=True) for row in rows],
        ),
        trace_id=get_trace_id(),
    )


@router.post("/documents/upload", response_model=ApiResponse[AdminKnowledgeUploadResponse], status_code=202)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    category: str = Form("faq"),
    permission_level: str = Form("public"),
    _user=Depends(require_permission("product:write")),
) -> ApiResponse[AdminKnowledgeUploadResponse]:
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail={"code": "KNOWLEDGE_FILE_TYPE_UNSUPPORTED", "message": "仅支持 TXT、MD、DOCX 文档上传"})
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail={"code": "KNOWLEDGE_FILE_EMPTY", "message": "上传文档不能为空"})
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"code": "KNOWLEDGE_FILE_TOO_LARGE", "message": "单个知识库文档不能超过 20MB"})
    if suffix in {".txt", ".md"}:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail={"code": "KNOWLEDGE_FILE_ENCODING_INVALID", "message": "TXT/MD 文档必须使用 UTF-8 编码"}) from exc
    upload_dir = Path("uploads") / "knowledge"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{suffix}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(content)
    repository = KnowledgeRepository(request.app.state.database)
    base = await repository.ensure_default_base()
    content_hash = hashlib.sha256(content).hexdigest()
    document_key = f"upload:{content_hash}:{uuid4().hex[:12]}"
    try:
        document, event_id = await repository.create_document(
            knowledge_base_id=str(base.id),
            document_key=document_key,
            file_name=original_name,
            source_path=str(stored_path.resolve()),
            file_type=suffix.lstrip("."),
            file_size=len(content),
            content_hash=content_hash,
            category=category.strip() or "faq",
            permission_level=permission_level.strip() or "public",
        )
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise
    # create_document writes a knowledge.document_created outbox event. The outbox publisher
    # sends it to Redis and knowledge_worker consumes it to parse, chunk and write pgvector chunks.
    return ApiResponse(
        data=AdminKnowledgeUploadResponse(
            document=DocumentResponse.model_validate(document, from_attributes=True),
            event_id=event_id,
            pipeline_status="queued",
        ),
        trace_id=get_trace_id(),
    )


@router.get("/default", response_model=ApiResponse[KnowledgeBaseResponse])
async def get_default_knowledge_base(request: Request, _user=Depends(require_permission("product:read"))) -> ApiResponse[KnowledgeBaseResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    base = await repository.ensure_default_base()
    return ApiResponse(data=KnowledgeBaseResponse(id=str(base.id), name=base.name, description=base.description, status=base.status), trace_id=get_trace_id())


@router.post("/documents", response_model=ApiResponse[DocumentResponse], status_code=201)
async def create_document(request: Request, payload: DocumentCreateRequest, _user=Depends(require_permission("product:write"))) -> ApiResponse[DocumentResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    base = await repository.ensure_default_base() if payload.knowledge_base_id is None else await repository.get_base(payload.knowledge_base_id)
    if base is None:
        raise ResourceNotFoundError("knowledge base not found", code="KNOWLEDGE_BASE_NOT_FOUND")
    document, _event_id = await repository.create_document(knowledge_base_id=str(base.id), document_key=payload.document_key, file_name=payload.file_name, source_path=payload.source_path, file_type=payload.file_type, file_size=payload.file_size, content_hash=payload.content_hash, category=payload.category, permission_level=payload.permission_level)
    return ApiResponse(data=DocumentResponse.model_validate(document, from_attributes=True), trace_id=get_trace_id())


@router.post("/search", response_model=ApiResponse[KnowledgeSearchResponse])
async def search_knowledge(request: Request, payload: KnowledgeSearchRequest, _user=Depends(require_permission("product:read"))) -> ApiResponse[KnowledgeSearchResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    provider = QwenProvider(request.app.state.settings)
    try:
        query_embedding = await provider.embed([payload.query])
        pairs = await repository.search_hybrid(query=payload.query, query_embedding=query_embedding[0], top_k=payload.top_k, knowledge_base_id=payload.knowledge_base_id, category=payload.category)
        mode = "hybrid-ftS-pgvector"
    except Exception:
        pairs = [(hit, 1.0) for hit in await repository.search_text(query=payload.query, top_k=payload.top_k, knowledge_base_id=payload.knowledge_base_id, category=payload.category)]
        mode = "text-ilike-fallback"
    finally:
        await provider.close()
    response = KnowledgeSearchResponse(query=payload.query, retrieval_mode=mode, embedding_dimension=request.app.state.settings.embedding_dimension, hits=[{"id": str(hit.id), "content": hit.content, "title_path": hit.title_path, "document_id": str(hit.document_id), "version_id": str(hit.version_id), "chunk_index": hit.chunk_index, "score": score, "metadata": hit.metadata_json} for hit, score in pairs])
    return ApiResponse(data=response, trace_id=get_trace_id())


@router.get("/documents/{document_id}/ingestion-status", response_model=ApiResponse[DocumentIngestionStatusResponse])
async def ingestion_status(request: Request, document_id: str, _user=Depends(require_permission("product:read"))) -> ApiResponse[DocumentIngestionStatusResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    document = await repository.get_document(document_id)
    if document is None:
        raise ResourceNotFoundError("knowledge document not found", code="KNOWLEDGE_DOCUMENT_NOT_FOUND")
    events = await repository.list_ingestion_events(document_id=document_id)
    return ApiResponse(
        data=DocumentIngestionStatusResponse(
            document_id=document_id,
            document_status=document.status,
            document_error=document.error_message,
            current_version_id=document.current_version_id,
            events=[{
                "id": str(event.id),
                "status": event.status,
                "attempt": event.attempt,
                "error_message": event.error_message,
                "created_at": event.created_at,
                "updated_at": event.updated_at,
                "published_at": event.published_at,
            } for event in events],
        ),
        trace_id=get_trace_id(),
    )


@router.post("/documents/{document_id}/ingest", response_model=ApiResponse[DocumentIngestResponse], status_code=202)
async def ingest_document(request: Request, document_id: str, _user=Depends(require_permission("product:write"))) -> ApiResponse[DocumentIngestResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    document = await repository.get_document(document_id)
    if document is None:
        raise ResourceNotFoundError("knowledge document not found", code="KNOWLEDGE_DOCUMENT_NOT_FOUND")
    event_id = await repository.enqueue_ingestion(document_id)
    return ApiResponse(data=DocumentIngestResponse(document_id=document_id, status="queued", event_id=event_id), trace_id=get_trace_id())
