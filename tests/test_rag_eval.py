import asyncio

from app.services.knowledge_retrieval import KnowledgeCitation
from scripts.run_rag_eval import _fragment_recall, _run_case, load_cases


def _cit(content: str, score: float) -> KnowledgeCitation:
    return KnowledgeCitation(chunk_id="c", document_id="d", version_id="v",
                             content=content, score=score, title_path="", page_number=None, metadata={})


class FakeRetriever:
    def __init__(self, contents: list[str], scores: list[float]):
        self.citations = [_cit(c, s) for c, s in zip(contents, scores)]

    async def search(self, **kwargs):
        return self.citations


def test_eval_set_shape():
    cases = load_cases("scripts/rag_eval_cases.json")
    assert cases, "评测集为空"
    for c in cases:
        assert c["id"] and c["query"] and c.get("gold_fragments")
        assert c.get("split") in {"train", "val", "test"}
    splits = {c["split"] for c in cases}
    assert {"train", "val", "test"} <= splits  # 三分法齐全


def test_fragment_recall_math():
    recall, hit = _fragment_recall(["我们服务客户规模一万名"], ["客户规模", "一万名", "不存在"])
    assert recall == 2 / 3
    assert hit is True
    recall2, hit2 = _fragment_recall(["完全无关内容"], ["客户规模", "一万名"])
    assert recall2 == 0.0
    assert hit2 is False


def test_run_case_metrics_with_fake_retriever():
    retriever = FakeRetriever(["公司累计服务客户超过一万名", "客服热线：400-xxx"], [0.8, 0.02])
    case = {"id": "x", "intent": "company_info", "query": "你们服务多少客户", "gold_fragments": ["客户", "一万名"]}
    r = asyncio.run(_run_case(retriever, case, top_k=2, rrf_k=60, min_score=0.1))
    assert r["hit"] is True
    assert r["recall"] == 1.0
    assert r["evidence"] is True  # 0.8 >= 0.1
    assert r["retrieved"] == 2
    assert r["top_scores"][0] == 0.8


def test_run_case_evidence_threshold():
    # 无引用超过 min_score → evidence=False（RAG 不采信）
    retriever = FakeRetriever(["低分相关内容片段"], [0.02])
    case = {"id": "y", "intent": "faq", "query": "随便", "gold_fragments": ["相关内容"]}
    r = asyncio.run(_run_case(retriever, case, top_k=1, rrf_k=60, min_score=0.1))
    assert r["evidence"] is False
