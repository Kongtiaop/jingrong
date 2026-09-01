import asyncio

from app.services.customer_rag_service import CustomerRagService
from app.services.understanding_config import UnderstandingConfig


class FakeRetriever:
    def __init__(self):
        self.captured_top_k = None

    async def search(self, *, query, query_embedding, top_k, scope):
        self.captured_top_k = top_k
        return []  # 无引用 → 走 handoff，便于测试 top_k 透传


def _fresh_config(top_k: int, min_score: float) -> UnderstandingConfig:
    c = UnderstandingConfig()
    c._data = {"retrieval": {"top_k": top_k, "rrf_k": 60, "min_score": min_score}}
    return c


def test_rag_top_k_comes_from_config():
    cfg = _fresh_config(top_k=7, min_score=0.5)
    retriever = FakeRetriever()
    svc = CustomerRagService(retriever, config=cfg)   # 注入独立配置，不污染共享单例
    assert svc.min_score == 0.5
    asyncio.run(svc.answer(query="查一下公司业务", intent="company_business"))
    assert retriever.captured_top_k == 7


def test_rag_config_fallback_when_unset():
    c = UnderstandingConfig()
    assert c.get_top_k() >= 1 and c.get_min_score() >= 0
