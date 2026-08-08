from __future__ import annotations

from typing import Any

from app.services.knowledge_retrieval import WealthKnowledgeRetriever

try:
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
    from pydantic import ConfigDict, Field
except ImportError:  # LangChain remains an optional runtime adapter.
    Document = None
    BaseRetriever = object


if Document is not None:
    class LangChainWealthRetriever(BaseRetriever):
        """Optional LangChain adapter over the application's own retriever."""

        model_config = ConfigDict(arbitrary_types_allowed=True)
        service: Any = Field(exclude=True)
        top_k: int = 5
        knowledge_base_id: str | None = None
        category: str | None = None

        async def _aget_relevant_documents(self, query: str, *, run_manager: Any = None) -> list[Document]:
            citations = await self.service.search(
                query=query,
                top_k=self.top_k,
                knowledge_base_id=self.knowledge_base_id,
                category=self.category,
            )
            return [
                Document(
                    page_content=item.content,
                    metadata={
                        "chunk_id": item.chunk_id,
                        "document_id": item.document_id,
                        "version_id": item.version_id,
                        "score": item.score,
                        "title_path": item.title_path,
                        "page_number": item.page_number,
                        **item.metadata,
                    },
                )
                for item in citations
            ]

        def _get_relevant_documents(self, query: str, *, run_manager: Any = None) -> list[Document]:
            raise RuntimeError("use async retrieval through LangChainWealthRetriever")
else:
    LangChainWealthRetriever = None


__all__ = ["LangChainWealthRetriever", "WealthKnowledgeRetriever"]
