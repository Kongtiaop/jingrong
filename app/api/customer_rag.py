from fastapi import APIRouter, Depends, Request

from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.knowledge import CustomerQueryRouteRequest, CustomerQueryRouteResponse, CustomerRagRequest, CustomerRagResponse
from app.services.customer_rag_handoff import CustomerRagHandoffService
from app.services.customer_rag_service import CustomerRagService
from app.services.customer_rag_answer import CustomerRagAnswerService
from app.services.customer_query_router import CustomerQueryRouter
from app.infrastructure.qwen import QwenProvider
from app.services.knowledge_retrieval import WealthKnowledgeRetriever

router = APIRouter(prefix="/customer-rag", tags=["customer-rag"])


@router.post("/route", response_model=ApiResponse[CustomerQueryRouteResponse])
async def customer_query_route(request: Request, payload: CustomerQueryRouteRequest, user=Depends(current_user)):
    """Return a deterministic, customer-scoped real-time fact when the query matches a supported route.

    The customer UI should call `/customer-rag/answer` only when this returns `rag_explanation`.
    """
    async with request.app.state.database.session_factory() as session:
        result = await CustomerQueryRouter().route(session=session, customer_id=user.id, query=payload.query)
    if result is None:
        return ApiResponse(
            data=CustomerQueryRouteResponse(
                source_type="rag_explanation", route="public_rag",
                answer="该问题不属于当前已接入的实时事实查询，将由客服知识库继续处理。",
                evidence_status="not_applicable",
                next_action="请调用客服知识问答接口获取引用回答。",
            ),
            trace_id=get_trace_id(),
        )
    return ApiResponse(
        data=CustomerQueryRouteResponse(
            source_type=result.source_type, route=result.route, answer=result.answer, data=result.data,
            evidence_status=result.evidence_status, next_action=result.next_action,
        ),
        trace_id=get_trace_id(),
    )


@router.post("/answer", response_model=ApiResponse[CustomerRagResponse])
async def customer_rag_answer(request: Request, payload: CustomerRagRequest, user=Depends(current_user)):
    retriever = WealthKnowledgeRetriever(KnowledgeRepository(request.app.state.database))
    provider = QwenProvider(request.app.state.settings)
    try:
        result = await CustomerRagService(retriever, embedding_provider=provider).answer(query=payload.query, intent=payload.intent, top_k=payload.top_k)
    finally:
        await provider.close()
    answer_provider = QwenProvider(request.app.state.settings)
    try:
        answer = await CustomerRagAnswerService(answer_provider).generate(query=payload.query, result=result)
    finally:
        await answer_provider.close()
    handoff: dict | None = None
    if result.handoff_required:
        handoff = await CustomerRagHandoffService(request.app.state.database).handoff(
            customer_id=user.id,
            query=payload.query,
            intent=payload.intent,
            evidence_status=result.evidence_status,
            citations=[],
        )
    return ApiResponse(
        data=CustomerRagResponse(
            intent=result.intent,
            answer_mode=result.answer_mode,
            evidence_status=result.evidence_status,
            handoff_required=result.handoff_required,
            message=result.message,
            citations=[
                {
                    "chunk_id": item.chunk_id,
                    "document_id": item.document_id,
                    "version_id": item.version_id,
                    "content": item.content,
                    "score": item.score,
                    "title_path": item.title_path,
                    "page_number": item.page_number,
                    "metadata": item.metadata,
                }
                for item in result.citations
            ],
            work_order_id=handoff["work_order_id"] if handoff else None,
            ticket_no=handoff["ticket_no"] if handoff else None,
            work_order_created=handoff["created"] if handoff else None,
            answer=answer.answer,
            generation_status=answer.generation_status,
        ),
        trace_id=get_trace_id(),
    )
