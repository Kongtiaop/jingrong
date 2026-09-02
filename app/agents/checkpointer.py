# -*- coding: utf-8 -*-
"""LangGraph Checkpointer 工厂（断点续跑）。

`SupervisorGraph.build(checkpointer=...)` 传入本模块产出的 checkpointer 即开启断点持久化。
- MemorySaver：进程内内存，适合测试/单节点/演示（**当前环境可用，已测**）。
- AsyncPostgresSaver：PostgreSQL 持久化，适合生产跨实例恢复；需要额外包
  `langgraph-checkpoint-postgres`（未装时会给出明确安装提示）并连上业务库。
"""
from __future__ import annotations

from typing import Any


def memory_checkpointer() -> Any:
    """进程内内存 Checkpointer（测试/单节点/演示）。"""
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def postgres_checkpointer(conninfo: str, *, loop: Any | None = None) -> Any:
    """PostgreSQL 持久化 Checkpointer（生产跨实例恢复）。

    依赖 `langgraph-checkpoint-postgres` 包与一个可用的 async 连接池。
    未装包会给出安装提示；装好后 `await cp.setup()` 建表即可用。
    """
    try:
        # v3.x 异步 saver 在 .aio 子模块；旧版在顶层
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver as _Saver
    except ImportError:
        try:
            from langgraph.checkpoint.postgres import AsyncPostgresSaver as _Saver
        except ImportError as exc:
            raise RuntimeError(
                "Postgres Checkpointer 需要额外依赖。请先：pip install 'langgraph-checkpoint-postgres' psycopg[binary] psycopg-pool"
            ) from exc
    from psycopg_pool import AsyncConnectionPool
    # autocommit=True：setup() 里 CREATE INDEX CONCURRENTLY 不能在事务块内执行
    pool = AsyncConnectionPool(conninfo=conninfo, max_size=5, kwargs={"autocommit": True})
    return _Saver(pool)


def thread_config(thread_id: str) -> dict:
    """生成携带 thread_id 的 RunnableConfig，供断点续跑定位同一会话状态。"""
    return {"configurable": {"thread_id": thread_id}}


def with_interrupt(config: dict, *, interrupt_before: list[str] | None = None,
                   interrupt_after: list[str] | None = None) -> dict:
    """在指定节点前/后设置中断点（配合 checkpointer 实现断点续跑）。"""
    cfg = dict(config or {})
    cfg["configurable"] = {**(cfg.get("configurable") or {})}
    if interrupt_before:
        cfg["configurable"]["interrupt_before"] = interrupt_before
    if interrupt_after:
        cfg["configurable"]["interrupt_after"] = interrupt_after
    return cfg
