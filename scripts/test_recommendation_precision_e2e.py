#!/usr/bin/env python
"""产品推荐精度 E2E（HTTP，跑真实 API 链路）。

验收点：
 1) 推荐返回为"精准 Top-N"：matches 数量 <= top_n(5)，且**每条**都带 recommendation_reason（逐条"为什么"）。
 2) 返回带 preference_note（说明总通过数 + 精选哪些）。
 3) "我能购买什么产品 / 能买哪些" 这类资格/推荐问句被路由到 产品推荐(matches)，
    而不是被误路由进 交易草稿(business_operation)。
 4) 用户在"我偏稳健、长期持有"后的偏好能在后续轮被读到（推荐理由提到稳健）。

用法（在能连到 API 的机器，例如服务器本机）：
  E2E_BASE=http://127.0.0.1:8010/api/v1 PYTHONPATH=. python scripts/test_recommendation_precision_e2e.py
退出码：0=通过；1=验收失败（会把关键字段打印出来便于排查）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8010/api/v1")
CUSTOMER = (os.environ.get("E2E_USER", "customer_demo"), os.environ.get("E2E_PASS", "Demo@2026Customer"))
TOP_N = 5

failures: list[str] = []


def _walk(node, key):
    """递归找所有 key 对应的值。"""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found.append(v)
            found.extend(_walk(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item, key))
    return found


def _find_matches(data):
    """找到形如 matches 的列表（元素含 product_name / recommendation_reason 之一）。"""
    for lst in _walk(data, "matches"):
        if isinstance(lst, list) and any(
            isinstance(m, dict) and (m.get("product_name") or m.get("name"))
            for m in lst
        ):
            return lst
    return None


async def execute(client, token, conv, content):
    r = await client.post(
        f"{BASE}/customer-service/sessions/agent/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": content, "conversation_id": conv, "page_context": "/customer-center"},
    )
    if r.status_code != 200:
        failures.append(f"execute {content!r} -> HTTP {r.status_code}: {r.text[:300]}")
        return None
    return r.json().get("data")


def check_recommendation(turn, data, expect_reasons_include=None):
    if data is None:
        return
    note_list = _walk(data, "preference_note")
    note = note_list[0] if note_list and isinstance(note_list[0], str) else ""
    matches = _find_matches(data)
    if not matches:
        failures.append(f"[{turn}] 未返回推荐 matches。note={note!r} keys={list(data.keys())[:8]}")
        return
    n = len(matches)
    print(f"[{turn}] matches={n} note={note[:120]}")
    if not (1 <= n <= TOP_N):
        failures.append(f"[{turn}] matches 数量 {n} 超出预期 [1,{TOP_N}]")
    for i, m in enumerate(matches):
        reason = m.get("recommendation_reason") or m.get("reasons") or ""
        if not reason:
            failures.append(f"[{turn}] match#{i} 缺少逐条 recommendation_reason: {m}")
        else:
            print(f"  - #{i} {m.get('product_name') or m.get('name')} :: {str(reason)[:80]}")
    if not note:
        failures.append(f"[{turn}] 缺少 preference_note")
    if expect_reasons_include:
        joined = " ".join(str(m.get("recommendation_reason") or "") for m in matches)
        if expect_reasons_include not in joined:
            failures.append(f"[{turn}] 期望理由包含 {expect_reasons_include!r}，未命中：{joined[:160]}")


async def main() -> int:
    conv = f"rec-e2e-{uuid.uuid4()}"
    async with httpx.AsyncClient(timeout=120) as client:
        login = await client.post(f"{BASE}/auth/login", json={"username": CUSTOMER[0], "password": CUSTOMER[1]})
        if login.status_code != 200:
            print(f"login failed: HTTP {login.status_code}: {login.text[:300]}", file=sys.stderr)
            return 2
        token = login.json()["data"]["access_token"]

        # 1) 普通推荐：应返回精准 Top-N + 逐条理由
        data = await execute(client, token, conv, "帮我推荐适合我的稳健理财产品")
        check_recommendation("普通推荐", data)

        # 3) 资格/推荐歧义：不应被误路由进交易草稿，而应返回产品推荐
        data = await execute(client, token, conv, "我能购买什么产品？适合我的买哪些")
        check_recommendation("资格/推荐歧义(能买什么)", data)

        # 4) 偏好记忆在后续轮生效：先声明偏好，再推荐 → 理由应提及"稳健"
        await execute(client, token, conv, "我偏稳健，想长期持有")
        data = await execute(client, token, conv, "再帮我推荐一下")
        check_recommendation("偏好记忆后推荐", data, expect_reasons_include="稳健")

    if failures:
        print("\n==== FAIL ====")
        for f in failures:
            print(" -", f)
        return 1
    print("\nRECOMMENDATION PRECISION E2E PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
