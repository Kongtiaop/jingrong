#!/usr/bin/env python
"""图版 vs 管线版双跑对照（切流前的验收工具）。

对同一批 query 分别跑：
  - 管线：CustomerAgentExecutionService.execute()   ← 当前生产默认
  - 图  ：GraphAgentRunner.execute()                ← 待切流候选

逐条比对 overall_status / route / answer / clarification / requires_handoff / task intents，
输出差异表与通过率。**有差异即为切流阻塞项**，需先定位是编排差异还是业务差异。

用法：
  python scripts/compare_graph_vs_pipeline.py
  python scripts/compare_graph_vs_pipeline.py --customer <user_id> --checkpoint

注：脚本会自动把仓库根目录加入 sys.path，因此本地与容器内（/app）都无需再设 PYTHONPATH。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

# `python scripts/x.py` 只会把 scripts/ 放进 sys.path，导致 `import app` 失败。
# 这里显式补上仓库根目录，本地与容器内均可直接运行。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 覆盖客户侧典型意图：事实查询 / 知识问答 / 澄清 / 转人工 / 交易草稿 / 推荐
DEFAULT_QUERIES: list[str] = [
    "查一下我的持仓",
    "我的资产有多少",
    "我的订单 ORD-123456 状态",
    "我的工单进展如何",
    "我有没有风险限制",
    "现金管理保本计划在售吗",
    "你们公司的主营业务是什么",
    "开户流程是怎样的",
    "什么是双录",
    "我要买1000元稳健产品",
    "推荐适合我的产品",
    "我想转人工客服",
    "你好",
    "这个那个",  # 期望触发澄清
]


@dataclass
class ComparisonRow:
    query: str
    pipeline_status: str
    graph_status: str
    diffs: dict = field(default_factory=dict)
    error: str | None = None


async def compare(*, customer_id: str, queries: list[str], use_checkpoint: bool,
                  conversation_prefix: str) -> list[ComparisonRow]:
    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.redis_client import RedisClient
    from app.services.customer_agent_execution import CustomerAgentExecutionService
    from app.services.graph_agent_runner import GraphAgentRunner, shadow_diff

    settings = get_settings()
    database = Database(settings)
    redis_client = RedisClient(settings)
    checkpointer = None

    if use_checkpoint:
        from app.agents.checkpointer import conninfo_from_database_url, ensure_postgres_checkpointer
        checkpointer = await ensure_postgres_checkpointer(
            conninfo_from_database_url(settings.database_url))
        print("checkpointer: postgres ready")

    rows: list[ComparisonRow] = []
    try:
        for index, query in enumerate(queries):
            # 每次独立 conversation_id：避免两条路径互相污染短时记忆
            conversation_id = f"{conversation_prefix}-{index}"
            pipeline_conv = f"{conversation_id}-pipe"
            graph_conv = f"{conversation_id}-graph"

            pipeline = await CustomerAgentExecutionService(
                database, redis_client.client, settings=settings
            ).execute(customer_id=customer_id, conversation_id=pipeline_conv, query=query)

            runner = GraphAgentRunner(database, redis_client.client, settings=settings,
                                      checkpointer=checkpointer)
            try:
                graph = await runner.execute(customer_id=customer_id, conversation_id=graph_conv,
                                             query=query, is_customer=True, user_id=customer_id)
                diff = shadow_diff(pipeline, graph)
                rows.append(ComparisonRow(
                    query=query, pipeline_status=pipeline.overall_status,
                    graph_status=graph.overall_status, diffs=diff))
            except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
                rows.append(ComparisonRow(
                    query=query, pipeline_status=pipeline.overall_status,
                    graph_status="<error>", error=f"{type(exc).__name__}: {exc}"))
    finally:
        if checkpointer is not None:
            try:
                await checkpointer.conn.close()
            except Exception:  # noqa: BLE001
                pass
        await redis_client.close()
        await database.dispose()
    return rows


def report(rows: list[ComparisonRow]) -> int:
    matched = [r for r in rows if not r.diffs and not r.error]
    mismatched = [r for r in rows if r.diffs or r.error]

    print("\n" + "=" * 78)
    print(f"{'query':<28} {'pipeline':<18} {'graph':<18} verdict")
    print("=" * 78)
    for row in rows:
        verdict = "OK" if not row.diffs and not row.error else "DIFF"
        print(f"{row.query[:27]:<28} {row.pipeline_status[:17]:<18} {row.graph_status[:17]:<18} {verdict}")

    if mismatched:
        print("\n--- 差异明细（切流阻塞项）---")
        for row in mismatched:
            print(f"\n[query] {row.query}")
            if row.error:
                print(f"  error: {row.error}")
            for field_name, values in row.diffs.items():
                print(f"  {field_name}:")
                print(f"    pipeline = {values.get('pipeline')!r}")
                print(f"    graph    = {values.get('graph')!r}")

    total = len(rows)
    print("\n" + "=" * 78)
    print(f"对照结果：{len(matched)}/{total} 完全一致，{len(mismatched)} 条存在差异")
    print("=" * 78)
    return 0 if not mismatched else 1


async def main() -> int:
    parser = argparse.ArgumentParser(description="图版 vs 管线版双跑对照")
    parser.add_argument("--customer", default="", help="客户 user_id（默认取第一个 demo 客户）")
    parser.add_argument("--query", action="append", default=None, help="自定义 query，可重复")
    parser.add_argument("--checkpoint", action="store_true", help="启用 Postgres checkpointer")
    parser.add_argument("--prefix", default="dualrun", help="conversation_id 前缀")
    args = parser.parse_args()

    customer_id = args.customer
    if not customer_id:
        from app.core.settings import get_settings
        from app.db.session import Database
        from sqlalchemy import select
        from app.models.auth import User, Role
        database = Database(get_settings())
        try:
            async with database.session_factory() as session:
                row = (await session.execute(
                    select(User).where(User.status == "active", User.roles.any(Role.code == "customer"))
                    .order_by(User.username).limit(1))).scalar_one_or_none()
                if row is None:
                    print("找不到可用的 demo 客户，请用 --customer 指定", file=sys.stderr)
                    return 2
                customer_id = row.id
                print(f"使用客户: {row.username} ({customer_id})")
        finally:
            await database.dispose()

    queries = args.query if args.query else DEFAULT_QUERIES
    rows = await compare(customer_id=customer_id, queries=queries,
                         use_checkpoint=args.checkpoint, conversation_prefix=args.prefix)
    return report(rows)


if __name__ == "__main__":
    # psycopg async 在 Windows 上需 SelectorEventLoop（Linux 默认即是）
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
