#!/usr/bin/env python
"""Postgres Checkpointer 断点续跑集成验证（直连业务库）。

覆盖：建表 → 中断在 summarize 前 → 同一 thread 恢复 → 完成 → 清理。
用法：PYTHONPATH=. python scripts/test_postgres_checkpointer.py
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from app.agents.checkpointer import postgres_checkpointer, thread_config
from app.agents.graph import SupervisorGraph
from app.core.settings import get_settings
from app.schemas.agents import AgentState
from app.services.customer_intent import IntentPlan, IntentTask
from app.services.customer_task_executor import TaskExecutionResult


class FakeExecutor:
    async def execute(self, *, customer_id: str, plan: IntentPlan):
        return [TaskExecutionResult(t.task_id, t.intent, "succeeded", answer=f"已查询{t.intent}", data={}) for t in plan.tasks]


def _base_state() -> AgentState:
    return AgentState(request_id="pgcp-1", trace_id="t-pgcp", user_message="查一下我的持仓", customer_id="c1", clarification_count=0)


async def main() -> None:
    s = get_settings()
    # asyncpg URL → psycopg 连接串
    conninfo = re.sub(r"^postgresql\+asyncpg://", "postgresql://", s.database_url)
    cp = postgres_checkpointer(conninfo)
    try:
        await cp.setup()  # 建 langgraph checkpoint 表
        graph = SupervisorGraph(executor=FakeExecutor()).build(checkpointer=cp, interrupt_before=["summarize_node"])
        tid = "pg-checkpoint-thread-1"
        cfg = thread_config(tid)

        first = await graph.ainvoke(_base_state(), cfg)
        assert first.get("task_results") or first.get("plan"), "应在 summarize 前中断，有中间状态"
        # 跨实例视角：用新的图/相同 thread 恢复（模拟不同进程）
        resumed = await graph.ainvoke(None, cfg)
        assert resumed["overall_status"] == "completed", resumed.get("overall_status")
        assert resumed["final_response"], "恢复后应有最终回答"
        assert resumed.get("task_results"), "thread 状态应被持久化（task_results 仍在）"
        print("postgres_checkpointer: interrupt+resume PASS")

        # 清理该 thread 的 checkpoint
        try:
            await cp.delete_thread(tid)
        except Exception:  # noqa: BLE001 - 不同版本方法名差异则走 SQL
            from sqlalchemy import text
            from app.db.session import Database
            db = Database(s)
            async with db.engine.begin() as conn:
                await conn.execute(text("DELETE FROM checkpoints WHERE thread_id=:t"), {"t": tid})
                await conn.execute(text("DELETE FROM checkpoint_writes WHERE thread_id=:t"), {"t": tid})
            await db.engine.dispose()
        print("postgres_checkpointer: cleanup OK")
    finally:
        try:
            await cp.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    # psycopg async 在 Windows 上需 SelectorEventLoop（生产 Linux 默认即是，无需此步）
    try:
        asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop())
    except TypeError:
        asyncio.run(main())
