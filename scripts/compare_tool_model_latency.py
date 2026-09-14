#!/usr/bin/env python
"""对比不同模型在「工具选择」这一任务上的延迟与准确性。

用途（ISSUE-20260914-TIER-S-ROUTES-TO-UNAUTHORIZED-MODEL 的后续决策依据）：
  - `qwen-turbo` 在本账号无权限（PermissionDeniedError），需从已开通模型里选替代；
  - 候选：`qwen-flash`（轻量快速）、`qwen-plus`（当前默认）、`qwen-max`（最强最慢）；
  - 工具轮此前用 qwen-plus 时出现 APITimeoutError（16.8s > 15s 超时），需评估换模型能否解决。

本脚本对每个模型：
  1. 跑一批 query，记录每次调用的耗时与选出的工具；
  2. 用「规则规划器给出的意图」作为**参考基线**，统计工具选择是否与之匹配；
  3. 输出平均/最大耗时与匹配率，供权衡「快」与「准」。

用法：
  python scripts/compare_tool_model_latency.py
  python scripts/compare_tool_model_latency.py --models qwen-flash qwen-plus
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# query -> 规则规划器期望的意图（作为工具选择的参考基线）
CASES: list[tuple[str, str]] = [
    ("我有没有风险限制", "risk_restriction"),
    ("查一下我的持仓", "asset_summary"),
    ("我的订单 ORD-123456 状态", "order_status"),
    ("我的工单进展如何", "workorder_status"),
    ("你们公司的主营业务是什么", "company_business"),
    ("开户流程是怎样的", "workflow"),
    ("什么是双录", "policy"),
    ("现金管理保本计划在售吗", "product_status"),
]

# 意图 -> 期望工具（与 customer_model_tools._INTENT_TO_TOOL 一致）
INTENT_TO_TOOL = {
    "risk_restriction": "get_my_risk_restriction",
    "asset_summary": "get_my_asset_summary",
    "order_status": "get_my_order_status",
    "workorder_status": "get_my_workorder_status",
    "product_status": "get_product_status",
    "company_info": "search_customer_knowledge",
    "company_business": "search_customer_knowledge",
    "workflow": "search_customer_knowledge",
    "policy": "search_customer_knowledge",
    "faq": "search_customer_knowledge",
}

SYSTEM = (
    "你是财富管理客服的只读查询调度器。基于用户问题，决定调用哪个只读工具获取事实依据。\n"
    "涉及客户个人数据（资产/订单/工单/风险限制）时用对应工具；"
    "知识类问题（公司业务/流程/政策/FAQ）用 search_customer_knowledge。"
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="工具选择模型的延迟与准确性对比")
    parser.add_argument("--models", nargs="*",
                        default=["qwen-flash", "qwen-plus", "qwen-max"])
    parser.add_argument("--repeat", type=int, default=1, help="每模型重复轮数（取平均更稳）")
    args = parser.parse_args()

    from app.core.settings import get_settings
    from app.infrastructure.qwen import QwenProvider
    from app.services.customer_model_tools import CUSTOMER_READ_TOOL_SCHEMAS

    settings = get_settings()
    provider = QwenProvider(settings)

    print(f"cases = {len(CASES)}   repeat = {args.repeat}")
    print("=" * 78)
    summary: list[tuple[str, float, float, str]] = []

    try:
        for model in args.models:
            durations: list[float] = []
            matched = 0
            total = 0
            denied = False
            print(f"\n=== {model} ===")
            for _ in range(args.repeat):
                for query, expected_intent in CASES:
                    expected_tool = INTENT_TO_TOOL.get(expected_intent)
                    t0 = time.monotonic()
                    try:
                        decision = await provider.request_tool_call(
                            system=SYSTEM, user=query,
                            tools=CUSTOMER_READ_TOOL_SCHEMAS, model=model)
                    except Exception as exc:  # noqa: BLE001
                        name = type(exc).__name__
                        if "PermissionDenied" in name:
                            denied = True
                            print(f"  {query:26s} 无权限（{name}）")
                        else:
                            print(f"  {query:26s} 异常 {name}: {str(exc)[:50]}")
                        continue
                    dt = (time.monotonic() - t0) * 1000
                    durations.append(dt)
                    total += 1
                    got = (decision or {}).get("name")
                    ok = got == expected_tool
                    matched += 1 if ok else 0
                    print(f"  {query:26s} {dt:7.0f}ms  期望={expected_tool:28s} 实际={got}  {'OK' if ok else 'MISS'}")

            if denied:
                summary.append((model, -1.0, -1.0, "无权限"))
                continue
            if not durations:
                summary.append((model, -1.0, -1.0, "无有效调用"))
                continue
            avg = sum(durations) / len(durations)
            p95 = sorted(durations)[int(len(durations) * 0.95) - 1] if len(durations) > 1 else durations[0]
            rate = f"{matched}/{total}"
            summary.append((model, avg, p95, rate))

        print("\n" + "=" * 78)
        print(f"{'model':<18} {'avg(ms)':>9} {'p95(ms)':>9} {'工具选择匹配':>12}")
        print("=" * 78)
        for model, avg, p95, rate in summary:
            if avg < 0:
                print(f"{model:<18} {'-':>9} {'-':>9} {rate:>12}")
            else:
                print(f"{model:<18} {avg:>9.0f} {p95:>9.0f} {rate:>12}")
        print("=" * 78)
        print("判读：在匹配率可接受的前提下选 avg 最小者；若都超时（>15000ms）则考虑放宽超时。")
    finally:
        await provider.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop()))
    except TypeError:
        raise SystemExit(asyncio.run(main()))
