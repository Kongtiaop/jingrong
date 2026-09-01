from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.customer_intent import IntentTask
from app.services.customer_task_executor import CustomerTaskExecutor
from app.services.customer_tools import CustomerReadToolResult


def test_bounded_react_refines_once_then_returns_evidence(monkeypatch):
    executor = CustomerTaskExecutor(database=None)
    calls = []

    async def fake_generate(*, query, result):
        assert result.citations  # 命中引用才生成
        return SimpleNamespace(answer="官方流程说明", generation_status="generated")

    # mock 生成服务，避免测试真调 LLM/网络
    monkeypatch.setattr("app.services.customer_task_executor.CustomerRagAnswerService",
                        lambda provider: SimpleNamespace(generate=fake_generate))

    async def fake_execute(**kwargs):
        calls.append(kwargs["query"])
        if len(calls) == 1:
            return CustomerReadToolResult(name="search_customer_knowledge", success=False, data={"evidence_status": "low"}, answer="资料不足", error="low_confidence")
        # 第二次：有真实引用（evidence_status=supported + 非空 citations）
        return CustomerReadToolResult(name="search_customer_knowledge", success=True, data={
            "evidence_status": "supported",
            "citations": [{"chunk_id": "c1", "document_id": "d1", "version_id": "v1",
                           "content": "适当性政策官方流程说明", "score": 0.9, "title_path": "政策",
                           "page_number": None, "metadata": {}}],
        }, answer="官方流程说明", error=None)

    executor.tools.execute = fake_execute
    item = IntentTask(task_id="react-1", intent="policy", route="customer_service", query="适当性政策", priority="high")
    result = asyncio.run(executor._bounded_react_knowledge(customer_id="customer-1", item=item))

    assert result.status == "succeeded"
    assert result.data["execution_mode"] == "bounded_react"
    assert result.data["generation_status"] == "generated"
    assert len(result.data["tool_calls"]) == 2
    assert all(set(call) <= {"tool", "iteration", "success", "error", "evidence_status"} for call in result.data["tool_calls"])
    assert calls[1].endswith("请提供官方流程、适用条件和注意事项")


def test_bounded_react_stops_after_non_retryable_failure():
    executor = CustomerTaskExecutor(database=None)
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs["query"])
        return CustomerReadToolResult(name="search_customer_knowledge", success=False, data={}, answer="未找到", error="fact_not_found")

    executor.tools.execute = fake_execute
    item = IntentTask(task_id="react-2", intent="faq", route="customer_service", query="未知问题", priority="high")
    result = asyncio.run(executor._bounded_react_knowledge(customer_id="customer-1", item=item))

    assert result.status == "needs_clarification"
    assert len(calls) == 1
    assert result.data["tool_calls"][0]["error"] == "fact_not_found"
