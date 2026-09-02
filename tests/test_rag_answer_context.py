"""回答上下文接入：CustomerRagAnswerService 支持 conversation_summary 注入 prompt（fake provider）。"""
import asyncio
from types import SimpleNamespace

from app.services.customer_rag_answer import CustomerRagAnswerService, CustomerRagAnswer
from app.services.customer_rag_service import CustomerRagResult
from app.services.knowledge_retrieval import KnowledgeCitation


class _FakeCompletions:
    def __init__(self, captured: dict):
        self.captured = captured

    async def create(self, **kwargs):
        self.captured["prompt"] = kwargs["messages"][0]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="根据资料，客户规模较大。"))])


class _FakeChat:
    def __init__(self, captured: dict):
        self.completions = _FakeCompletions(captured)


class _FakeClient:
    def __init__(self, captured: dict):
        self.chat = _FakeChat(captured)


class _FakeSettings:
    qwen_chat_model = "fake"


class _FakeProvider:
    def __init__(self, captured: dict):
        self.settings = _FakeSettings()
        self._client = _FakeClient(captured)


def _result():
    return CustomerRagResult(
        intent="company_info", answer_mode="rag_answer", evidence_status="sufficient",
        citations=[KnowledgeCitation(
            chunk_id="c1", document_id="d1", version_id="v1", content="公司客户规模超过 100 万。",
            score=0.9, title_path="公司介绍", page_number=1, metadata={},
        )],
        handoff_required=False, message="")


def test_answer_injects_conversation_summary_into_prompt():
    captured = {}
    provider = _FakeProvider(captured)
    svc = CustomerRagAnswerService(provider)
    out = asyncio.run(svc.generate(query="我们公司服务多少客户", result=_result(), conversation_summary='{"concern":"客户规模","resolved":"","pending":"","preference_hint":""}'))
    assert out.generation_status == "generated"
    assert "客户近期对话摘要" in captured["prompt"]
    assert "客户规模" in captured["prompt"]
    # 仍强制依据引用回答
    assert "只能根据提供的引用回答" in captured["prompt"]


def test_answer_without_summary_no_history_block():
    captured = {}
    svc = CustomerRagAnswerService(_FakeProvider(captured))
    asyncio.run(svc.generate(query="我们服务多少客户", result=_result()))
    assert "客户近期对话摘要" not in captured["prompt"]


def test_handoff_short_circuits_generation():
    svc = CustomerRagAnswerService(_FakeProvider({}))
    out = asyncio.run(svc.generate(query="q", result=CustomerRagResult(intent="faq", answer_mode="rag_answer", evidence_status="low_confidence", citations=[], handoff_required=True, message="转人工"), conversation_summary="x"))
    assert out.generation_status == "not_generated"
