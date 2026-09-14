#!/usr/bin/env python
"""模型工具轮的根因诊断：直接打印容器内的真实中间值。

之所以需要它：此前三次修复都在**本地模拟**下验证通过，但容器内现象不变。
说明我验证的前提与真实运行不符。本脚本不再模拟，直接跑真实链路并暴露：
  1. planned_intents 实际内容（是否为空？）
  2. _uncovered_intents 的实际返回值
  3. 模型每轮的决策
  4. stopped_reason / _fill_uncovered 是否被调用
  5. 最终 results 的 intent 与 execution_mode

用法：
  python scripts/diagnose_model_tool_loop.py --query "我有没有风险限制"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main() -> int:
    parser = argparse.ArgumentParser(description="模型工具轮根因诊断")
    parser.add_argument("--query", required=True)
    parser.add_argument("--customer", default="")
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.db.session import Database
    from app.infrastructure.qwen import QwenProvider
    from app.infrastructure.redis_client import RedisClient
    from app.services import customer_model_tools as cmt
    from app.services.customer_intent import CustomerIntentPlanner
    from app.services.customer_tools import CustomerReadTools

    settings = get_settings()
    database = Database(settings)
    redis_client = RedisClient(settings)
    provider = QwenProvider(settings)

    print("=" * 78)
    print("STEP 0：配置")
    print("=" * 78)
    print(f"  agent_model_tool_selection_enabled = {settings.agent_model_tool_selection_enabled}")
    print(f"  agent_model_tool_max_rounds        = {settings.agent_model_tool_max_rounds}")

    customer_id = args.customer
    if not customer_id:
        from sqlalchemy import select
        from app.models.auth import User, Role
        async with database.session_factory() as session:
            row = (await session.execute(
                select(User).where(User.status == "active", User.roles.any(Role.code == "customer"))
                .order_by(User.username).limit(1))).scalar_one_or_none()
            if row is None:
                print("找不到 demo 客户", file=sys.stderr)
                await redis_client.close(); await database.dispose(); await provider.close()
                return 2
            customer_id = row.id
            print(f"  customer = {row.username} ({customer_id})")

    print()
    print("=" * 78)
    print("STEP 1：规划器产出（planned_intents 的来源）")
    print("=" * 78)
    plan = CustomerIntentPlanner().plan(args.query)
    planned = [t.intent for t in plan.tasks]
    print(f"  plan.tasks = {planned}")
    print(f"  => 传进 loop 的 planned_intents = {planned}")
    if not planned:
        print("  ⚠️ planned_intents 为空 → 兜底永远不触发（这是根因候选之一）")

    print()
    print("=" * 78)
    print("STEP 2：_INTENT_TO_TOOL 映射（决定能否补齐）")
    print("=" * 78)
    for intent in planned:
        tool = cmt._INTENT_TO_TOOL.get(intent)
        in_allow = tool in cmt._ALLOWED_TOOL_NAMES if tool else False
        print(f"  {intent:22s} -> {tool}   (在白名单内={in_allow})")

    print()
    print("=" * 78)
    print("STEP 3：真实执行模型工具轮（带逐轮追踪）")
    print("=" * 78)
    tools = CustomerReadTools(database)
    loop = cmt.CustomerModelToolLoop(
        provider=provider, tools=tools, database=database,
        max_rounds=settings.agent_model_tool_max_rounds)

    # 包装 request_tool_call 以追踪模型每轮决策
    original_req = provider.request_tool_call
    round_no = {"n": 0}

    async def traced(*, system, user, tools, model=None, temperature=0.0):
        round_no["n"] += 1
        decision = await original_req(system=system, user=user, tools=tools,
                                      model=model, temperature=temperature)
        has_planned = "该问题经规则分析需要覆盖" in user
        has_pending = "尚未取得依据" in user
        print(f"  [round {round_no['n']}] 模型决策 = {decision}")
        print(f"             user 含 planned_block={has_planned}  含缺失追问={has_pending}")
        return decision

    provider.request_tool_call = traced
    try:
        outcome = await loop.run(customer_id=customer_id, conversation_id=f"diag-{args.query[:8]}",
                                 query=args.query, planned_intents=planned)
    finally:
        provider.request_tool_call = original_req

    print()
    print("=" * 78)
    print("STEP 4：结果")
    print("=" * 78)
    print(f"  stopped_reason = {outcome.stopped_reason}")
    print(f"  tool_names     = {outcome.tool_names}")
    print(f"  rounds         = {[(r.round_index, r.tool, r.success, r.arguments.get('filled_by')) for r in outcome.rounds]}")
    print(f"  results        = {[(r['intent'], r['data'].get('execution_mode')) for r in outcome.results]}")
    print()
    after = cmt._uncovered_intents(planned, outcome.results)
    print(f"  事后算未覆盖意图 = {after}")
    if after:
        print("  ⚠️ 仍有未覆盖意图 → 兜底未生效或补齐失败")
    else:
        print("  ✅ 全部覆盖")

    await provider.close()
    await redis_client.close()
    await database.dispose()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
