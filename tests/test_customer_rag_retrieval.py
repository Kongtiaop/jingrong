import asyncio

from app.services.customer_rag_service import CustomerRagService
from app.services.knowledge_retrieval import KnowledgeCitation


class FakeRetriever:
    def __init__(self):
        self.query_embedding = None

    async def search(self, **kwargs):
        self.query_embedding = kwargs.get("query_embedding")
        return [KnowledgeCitation("c1", "d-company", "v1", "客户规模：累计服务客户超过一万名。", 0.02, "客户规模", None, {})]


class FakeEmbedding:
    async def embed(self, texts):
        assert texts
        return [[0.1] * 1024]


def test_customer_scale_uses_query_embedding():
    retriever = FakeRetriever()
    result = asyncio.run(CustomerRagService(retriever, FakeEmbedding()).answer(query="查一下你们公司的客户规模", intent="company_info"))
    assert retriever.query_embedding is not None
    assert "客户规模" in result.citations[0].content
    assert result.evidence_status == "sufficient"
