# -*- coding: utf-8 -*-
"""SubagentExecutor 单元测试：并发上限、超时降级、失败隔离、只读白名单、融合、审计。"""
import asyncio
from typing import Awaitable

from app.schemas.subagent import SubagentResult, SubagentTask
from app.services.subagent_executor import READ_ONLY_CAPABILITIES, SubagentExecutor


def _task(task_id: str, capability: str = "customer_asset_summary") -> SubagentTask:
    return SubagentTask(task_id=task_id, query="q", read_only_capability=capability)


def _ok(task_id: str, conf: float = 0.9, summary: str = "ok") -> SubagentResult:
    return SubagentResult(task_id=task_id, status="success", summary=summary, confidence=conf)


async def _runner_ok(task: SubagentTask) -> SubagentResult:
    return _ok(task.task_id, summary=f"res-{task.task_id}")


class _Audit:
    def __init__(self):
        self.rows: list[tuple[str, str]] = []

    async def __call__(self, task: SubagentTask, result: SubagentResult, dur: float):
        self.rows.append((task.read_only_capability, result.status))


def test_concurrency_limit_is_respected():
    running = 0
    peak = 0

    async def runner(t: SubagentTask) -> SubagentResult:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return _ok(t.task_id)

    async def go():
        ex = SubagentExecutor(runner=runner, max_concurrent=2)
        return await ex.execute([_task(f"t{i}") for i in range(6)])

    res = asyncio.run(go())
    assert peak <= 2
    assert all(r.status == "success" for r in res.results)


def test_per_task_timeout_degrades_without_breaking_others():
    async def runner(t: SubagentTask) -> SubagentResult:
        if t.task_id == "slow":
            await asyncio.sleep(10)
        return _ok(t.task_id)

    async def go():
        ex = SubagentExecutor(runner=runner, max_concurrent=4, per_task_timeout_s=0.05, global_timeout_s=2)
        return await ex.execute([_task("slow"), _task("fast", "customer_product_status")])

    res = asyncio.run(go())
    by = {r.task_id: r for r in res.results}
    assert by["slow"].status == "partial"
    assert "subagent_timeout" in by["slow"].error
    assert by["fast"].status == "success"
    assert res.overall_status == "partial"


def test_one_failure_does_not_fail_whole():
    async def runner(t: SubagentTask) -> SubagentResult:
        if t.task_id == "boom":
            raise RuntimeError("boom")
        return _ok(t.task_id)

    async def go():
        ex = SubagentExecutor(runner=runner, max_concurrent=4)
        return await ex.execute([_task("boom"), _task("fine")])

    res = asyncio.run(go())
    by = {r.task_id: r for r in res.results}
    assert by["boom"].status == "failed"
    assert "RuntimeError" in by["boom"].error
    assert by["fine"].status == "success"
    assert res.overall_status == "partial"


def test_non_allowlisted_capability_rejected():
    async def runner(t: SubagentTask) -> SubagentResult:
        return _ok(t.task_id)

    async def go():
        ex = SubagentExecutor(runner=runner)
        return await ex.execute([_task("x", capability="money_transfer")])

    res = asyncio.run(go())
    assert res.results[0].status == "failed"
    assert "read_only_capability_not_allowed" in res.results[0].error


def test_whitelist_is_read_only_only():
    # 白名单不得出现任何写/资金/订单执行类能力
    banned_substrings = ("transfer", "trade", "order_submit", "redeem", "drawdown", "write", "handle")
    assert READ_ONLY_CAPABILITIES
    for cap in READ_ONLY_CAPABILITIES:
        assert not any(b in cap.lower() for b in banned_substrings), cap


def test_merge_is_stable_and_ranked():
    async def runner(t: SubagentTask) -> SubagentResult:
        return _ok(t.task_id, conf={"lo": 0.5, "hi": 0.95}.get(t.task_id, 0.5), summary=f"s-{t.task_id}")

    async def go():
        ex = SubagentExecutor(runner=runner)
        return await ex.execute([_task("lo"), _task("hi")])

    res = asyncio.run(go())
    assert res.overall_status == "succeeded"
    # 高置信在前
    assert res.answer.index("[hi]") < res.answer.index("[lo]")
    # 稳定：跑两次一致
    res2 = asyncio.run(go())
    assert res2.answer == res.answer


def test_audit_called_per_task():
    audit = _Audit()
    async def go():
        ex = SubagentExecutor(runner=_runner_ok, max_concurrent=2, audit=audit)
        return await ex.execute([_task("a"), _task("b", "customer_product_status")])

    asyncio.run(go())
    assert len(audit.rows) == 2
    assert set(audit.rows) == {("customer_asset_summary", "success"), ("customer_product_status", "success")}


def test_empty_tasks():
    async def go():
        return await SubagentExecutor(runner=_runner_ok).execute([])

    res = asyncio.run(go())
    assert res.results == []
    assert res.overall_status == "succeeded"


def test_missing_runner_raises():
    async def go():
        return await SubagentExecutor().execute([_task("a")])

    try:
        asyncio.run(go())
        assert False, "expected ValueError"
    except ValueError:
        pass


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    def add(self, row):
        self._sink.append(row)

    async def commit(self):
        return None


class _FakeFactory:
    def __init__(self, sink):
        self._sink = sink

    def __call__(self):
        class _Ctx:
            def __init__(self, sink):
                self.sink = sink

            async def __aenter__(self):
                return _FakeSession(self.sink)

            async def __aexit__(self, *_a):
                return False

        return _Ctx(self._sink)


def test_service_run_parallel_read_only_fans_out_and_audits():
    # 走真实 CustomerAgentExecutionService.run_parallel_read_only：验证 fan-out 并发 + AuditLog(subagent.ran) + 融合
    from app.services.customer_agent_execution import CustomerAgentExecutionService

    audit_sink: list = []
    peak = 0

    async def runner(t: SubagentTask) -> SubagentResult:
        nonlocal peak
        # 简单并发计测（不精确，仅确保不会串行崩溃）
        peak += 1
        await asyncio.sleep(0.01)
        peak -= 1
        return _ok(t.task_id, summary=f"R-{t.task_id}")

    db = type("DB", (), {"session_factory": _FakeFactory(audit_sink)})()
    svc = CustomerAgentExecutionService(db, None, settings=type("S", (), {"llm_intent_fallback_timeout_seconds": 1})())

    tasks = [_task("a", "customer_asset_summary"), _task("b", "customer_product_status")]
    res = asyncio.run(svc.run_parallel_read_only(customer_id="c1", conversation_id="cv1", subtasks=tasks, runner=runner, max_concurrent=2))

    assert res.overall_status == "succeeded"
    assert {r.task_id for r in res.results} == {"a", "b"}
    assert all(r.status == "success" for r in res.results)
    assert "R-a" in res.answer and "R-b" in res.answer
    # AuditLog(action="subagent.ran") 已写入，且只读标记为 True
    audits = [r for r in audit_sink if getattr(r, "action", "") == "subagent.ran"]
    assert len(audits) == 2
    assert all("customer_service_conversation" == getattr(a, "resource_type", "") for a in audits)
    assert all("subagent.ran" == a.action for a in audits)
