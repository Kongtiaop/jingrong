from __future__ import annotations

import asyncio

from app.services.customer_intent import IntentTask
from app.services.customer_task_executor import CustomerTaskExecutor
from app.services.customer_tools import CustomerReadToolResult


def test_bounded_react_refines_once_then_returns_evidence():
    executor = CustomerTaskExecutor(database=None)
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs["query"])
        if len(calls) == 1:
            return CustomerReadToolResult(name="search_customer_knowledge", success=False, data={"evidence_status": "low"}, answer="资料不足", error="low_confidence")
        return CustomerReadToolResult(name="search_customer_knowledge", success=True, data={"evidence_status": "supported", "citations": []}, answer="官方流程说明", error=None)

    executor.tools.execute = fake_execute
    item = IntentTask(task_id="react-1", intent="policy", route="customer_service", query="适当性政策", priority="high")
    result = asyncio.run(executor._bounded_react_knowledge(customer_id="customer-1", item=item))

    assert result.status == "succeeded"
    assert result.data["execution_mode"] == "bounded_react"
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
