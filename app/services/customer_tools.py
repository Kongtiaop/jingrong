from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.customer_query_router import CustomerQueryRouter
from app.infrastructure.qwen import QwenProvider
from app.core.settings import get_settings
from app.repositories.knowledge import KnowledgeRepository
from app.services.customer_rag_service import CustomerRagService
from app.services.knowledge_retrieval import WealthKnowledgeRetriever


@dataclass(frozen=True)
class CustomerReadToolResult:
    name: str
    success: bool
    data: dict[str, Any]
    answer: str
    error: str | None = None


class CustomerReadTools:
    """Controlled read-only tool facade. Tools never access repositories directly."""

    def __init__(self, database):
        self.database = database

    async def execute(self, *, name: str, customer_id: str, query: str, intent: str | None = None, entities: dict[str, str] | None = None) -> CustomerReadToolResult:
        allowed = {
            "get_my_workorder_status", "get_my_order_status", "get_my_asset_summary",
            "get_my_risk_restriction", "get_product_status", "search_customer_knowledge", "explain_order_status", "explain_workorder_status",
        }
        if name not in allowed:
            return CustomerReadToolResult(name=name, success=False, data={}, answer="", error="tool_not_allowed")
        if name in {"search_customer_knowledge", "explain_order_status", "explain_workorder_status"}:
            selected_intent = intent or ("workflow" if name != "search_customer_knowledge" else "faq")
            retriever = WealthKnowledgeRetriever(KnowledgeRepository(self.database))
            rag = await CustomerRagService(retriever, embedding_provider=QwenProvider(get_settings())).answer(query=query, intent=selected_intent)
            return CustomerReadToolResult(name=name, success=not rag.handoff_required, data={"citations": [item.__dict__ for item in rag.citations], "evidence_status": rag.evidence_status}, answer=rag.message, error="low_confidence" if rag.handoff_required else None)
        async with self.database.session_factory() as session:
            result = await CustomerQueryRouter().route(session=session, customer_id=customer_id, query=query, entities=entities)
        if result is None:
            return CustomerReadToolResult(name=name, success=False, data={}, answer="", error="fact_not_found")
        expected = {
            "get_my_workorder_status": "workorder_status", "get_my_order_status": "order_status",
            "get_my_asset_summary": "asset_summary", "get_my_risk_restriction": "risk_restriction",
            "get_product_status": "product_status",
        }[name]
        if result.route != expected:
            return CustomerReadToolResult(name=name, success=False, data={}, answer="", error="route_mismatch")
        if result.evidence_status in {"needs_clarification", "not_found"}:
            return CustomerReadToolResult(name=name, success=False, data=result.data, answer=result.answer, error="ambiguous_product" if result.evidence_status == "needs_clarification" else "fact_not_found")
        return CustomerReadToolResult(name=name, success=True, data=result.data, answer=result.answer)


