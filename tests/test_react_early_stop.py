from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.customer_intent import IntentTask
from app.services.customer_task_executor import CustomerTaskExecutor
from app.services.customer_tools import CustomerReadToolResult


def _item(tid: str, query: str, intent: str = "policy") -> IntentTask:
    return IntentTask(task_id=tid, intent=intent, route="customer_service", query=query, priority="high")


def test_repeated_result_early_stops():
    """两次调用返回相同结果（同 hash）→ 判定无进展 → 早停，不跑满上限。"""
    executor = CustomerTaskExecutor(database=None)
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs["query"])
        return CustomerReadToolResult(
            name="search_customer_knowledge", success=False,
            data={"evidence_status": "low", "citations": []},
            answer="资料不足", error="low_confidence",
        )

    executor.tools.execute = fake_execute
    result = asyncio.run(executor._bounded_react_knowledge(customer_id="c1", item=_item("es-1", "政策问题")))

    assert result.status == "needs_clarification"
    assert len(result.data["tool_calls"]) == 2          # 第 2 轮发现重复即停，而非跑满 20
    assert result.data["tool_calls"][-1].get("early_stop") == "repeated_result"


def test_changing_results_do_not_early_stop_until_success():
    """结果在变化 → 不早停；第 2 轮拿到有效引用 → 正常 succeeded。"""
    executor = CustomerTaskExecutor(database=None)
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs["query"])
        if len(calls) == 1:
            return CustomerReadToolResult(
                name="search_customer_knowledge", success=False,
                data={"evidence_status": "low", "citations": []},
                answer="A", error="low_confidence",
            )
        return CustomerReadToolResult(
            name="search_customer_knowledge", success=True,
            data={"evidence_status": "supported", "citations": [{
                "chunk_id": "c1", "document_id": "d1", "version_id": "v1",
                "content": "官方流程", "score": 1.0, "title_path": "政策", "page_number": 1, "metadata": {},
            }]},
            answer="B", error=None,
        )

    executor.tools.execute = fake_execute
    fake_gen = MagicMock()
    fake_gen.generate = AsyncMock(return_value=SimpleNamespace(answer="生成回答", generation_status="generated"))
    with patch("app.services.customer_task_executor.CustomerRagAnswerService", return_value=fake_gen):
        result = asyncio.run(executor._bounded_react_knowledge(customer_id="c1", item=_item("es-2", "政策问题")))

    assert result.status == "succeeded"
    assert result.data["execution_mode"] == "bounded_react"
    assert result.data["tool_calls"][-1].get("early_stop") is None   # 没有早停
    assert len(result.data["tool_calls"]) == 2


def test_non_retryable_failure_no_early_stop():
    """非 low_confidence 失败 → 1 次即停，不算早停（结果没重复）。"""
    executor = CustomerTaskExecutor(database=None)
    calls = []

    async def fake_execute(**kwargs):
        calls.append(kwargs["query"])
        return CustomerReadToolResult(
            name="search_customer_knowledge", success=False,
            data={}, answer="未找到", error="fact_not_found",
        )

    executor.tools.execute = fake_execute
    result = asyncio.run(executor._bounded_react_knowledge(customer_id="c1", item=_item("es-3", "未知问题")))

    assert result.status == "needs_clarification"
    assert len(calls) == 1
    assert result.data["tool_calls"][0].get("early_stop") is None
