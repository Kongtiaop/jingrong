#!/usr/bin/env python
"""单条 query 的图版 vs 管线版 task 级明细对比（定位差异根因用）。

`compare_graph_vs_pipeline.py` 只比对 `overall_status` 等聚合字段，看不到"哪个 task 失败、
为什么失败"。本脚本把两边的 `results[]` 逐 task 打出来，用于判断差异是：
  - 编排差异（task 集合/状态不同）
  - 证据差异（同一 task 一方 succeeded 一方 needs_clarification）
  - 纯措辞差异（状态一致、只有 answer 文本不同）

用法：
  python scripts/inspect_query_dual.py --query "你们公司的主营业务是什么"
  python scripts/inspect_query_dual.py --query "..." --customer <user_id>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _dump(label: str, resp) -> None:
    print(f"\n{'=' * 78}")
    print(f"[{label}] overall_status={resp.overall_status}  route={resp.route}  "
          f"requires_handoff={resp.requires_handoff}")
    print(f"[{label}] clarification={resp.clarification!r}")
    print(f"[{label}] ticket_no={resp.ticket_no}")
    print(f"[{label}] answer={resp.answer[:200]!r}{'...' if len(resp.answer) > 200 else ''}")
    print(f"{'-' * 78}")
    if not resp.results:
        print(f"[{label}] (无 task 结果)")
    for r in resp.results:
        data = r.data or {}
        tool_calls = data.get("tool_calls") or []
        rec = data.get("recommendation") or {}
        n_citations = len(data.get("citations") or [])
        print(f"[{label}] task={r.task_id} intent={r.intent}")
        print(f"{'':{len(label) + 3}}status={r.status}  error={r.error}  "
              f"evidence={data.get('evidence_status')}  citations={n_citations}  "
              f"tool_calls={len(tool_calls)}  execution_mode={data.get('execution_mode')}")
        print(f"{'':{len(label) + 3}}answer={r.answer[:160]!r}{'...' if len(r.answer) > 160 else ''}")
        if r.clarification:
            print(f"{'':{len(label) + 3}}clarification={r.clarification!r}")
        for i, call in enumerate(tool_calls, 1):
            print(f"{'':{len(label) + 3}}  call[{i}] tool={call.get('tool')} "
                  f"success={call.get('success')} error={call.get('error')} "
                  f"evidence={call.get('evidence_status')}")
            # 打参数：区分"同一工具不同改写"与"重复同一调用"必须看 args
            args = call.get("arguments")
            if args:
                print(f"{'':{len(label) + 5}}args={args}")
        # 模型调度循环的停止原因：判断"为什么停了"（主动停 / 越界 / 重复 / 预算耗尽）
        trace = data.get("model_tool_trace") or {}
        if trace:
            print(f"{'':{len(label) + 3}}model_tool: rounds={len(trace.get('rounds') or [])} "
                  f"stopped_reason={trace.get('stopped_reason')!r}")
            for step in trace.get("decisions") or []:
                print(f"{'':{len(label) + 5}}decision[r{step.get('round')}] "
                      f"tool={step.get('tool')} outcome={step.get('outcome')} "
                      f"args={step.get('arguments')}")
                if step.get("reasoning"):
                    print(f"{'':{len(label) + 7}}reasoning={step['reasoning']!r}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="单条 query 双路径 task 级明细对比")
    parser.add_argument("--query", required=True)
    parser.add_argument("--customer", default="")
    parser.add_argument("--checkpoint", action="store_true")
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.redis_client import RedisClient
    from app.services.customer_agent_execution import CustomerAgentExecutionService
    from app.services.graph_agent_runner import GraphAgentRunner

    settings = get_settings()
    database = Database(settings)
    redis_client = RedisClient(settings)
    checkpointer = None

    customer_id = args.customer
    if not customer_id:
        from sqlalchemy import select
        from app.models.auth import User, Role
        async with database.session_factory() as session:
            row = (await session.execute(
                select(User).where(User.status == "active", User.roles.any(Role.code == "customer"))
                .order_by(User.username).limit(1))).scalar_one_or_none()
            if row is None:
                print("找不到可用 demo 客户，请用 --customer 指定", file=sys.stderr)
                await database.dispose()
                return 2
            customer_id = row.id
            print(f"使用客户: {row.username} ({customer_id})")

    if args.checkpoint:
        from app.agents.checkpointer import conninfo_from_database_url, ensure_postgres_checkpointer
        checkpointer = await ensure_postgres_checkpointer(
            conninfo_from_database_url(settings.database_url))

    tag = uuid4().hex[:8]
    try:
        print(f"\nquery = {args.query!r}")
        pipeline = await CustomerAgentExecutionService(
            database, redis_client.client, settings=settings
        ).execute(customer_id=customer_id, conversation_id=f"inspect-{tag}-pipe", query=args.query)
        _dump("pipeline", pipeline)

        runner = GraphAgentRunner(database, redis_client.client, settings=settings,
                                  checkpointer=checkpointer)
        graph = await runner.execute(customer_id=customer_id,
                                     conversation_id=f"inspect-{tag}-graph",
                                     query=args.query, is_customer=True, user_id=customer_id)
        _dump("graph", graph)

        print(f"\n{'=' * 78}")
        print("判定")
        print(f"{'=' * 78}")
        p_intents = [(r.intent, r.status) for r in pipeline.results]
        g_intents = [(r.intent, r.status) for r in graph.results]
        if p_intents != g_intents:
            print(f"  task 级差异：\n    pipeline={p_intents}\n    graph   ={g_intents}")
        else:
            print("  task 级完全一致（差异仅在 answer 文本/聚合状态）")
        print(f"  overall: pipeline={pipeline.overall_status} graph={graph.overall_status}")
        print(f"  handoff: pipeline={pipeline.requires_handoff} graph={graph.requires_handoff}")
    finally:
        if checkpointer is not None:
            try:
                await checkpointer.conn.close()
            except Exception:  # noqa: BLE001
                pass
        await redis_client.close()
        await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
