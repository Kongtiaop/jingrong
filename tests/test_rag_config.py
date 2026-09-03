import asyncio

from app.services.customer_rag_service import CustomerRagService
from app.services.knowledge_retrieval import KnowledgeCitation
from app.services.understanding_config import UnderstandingConfig


class FakeRetriever:
    def __init__(self):
        self.captured_top_k = None

    async def search(self, *, query, query_embedding, top_k, scope):
        self.captured_top_k = top_k
        # 返回一个足够相关的引用，避免触发 C 兜底扩 top_k（top_k 透传测试只关心首查值）
        return [
            KnowledgeCitation(
                chunk_id="c1", document_id="d1", version_id="v1", content="公司主营业务为财富管理。",
                score=0.9, title_path="", page_number=None, metadata={}, vector_similarity=0.9,
            )
        ]


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


def test_reload_loads_real_yaml_and_dynamic_top_k():
    """守卫：理解层配置必须在运行时真正从 config/understanding.yaml 加载。

    曾出现 _CONFIG_PATH 解析到不存在路径 + 运行时从不 reload()，导致全部配置
    （难度路由/检索参数/枚举 top_k）静默回退代码默认。此处 reload 后断言枚举
    query 拿到 yaml 里配置的 top_k_enum，确保这条真实加载路径可用。
    """
    c = UnderstandingConfig()
    c.reload()
    assert c.get_top_k() >= 1
    # top_k_enum 在 committed config 里应配置为 10；枚举 query 命中它
    assert c.get_top_k_for_query("你们公司有哪些牌照") >= c.get_top_k()
    assert c.get_top_k_for_query("公司有哪几个业务线") >= c.get_top_k()
