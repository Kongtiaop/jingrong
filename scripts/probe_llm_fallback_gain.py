#!/usr/bin/env python
"""线上探针：验证"非交易 LLM 意图兜底"的增益（fallback=ON 时）。

原理：构造一批**当前正则规则覆盖不到、但其实是推荐意图**的问句。
  - 若兜底关闭：这些会被规则路由成 unknown/澄清（或偶尔错路由成其他）。
  - 若兜底开启：高置信 LLM 应把它们提升为"产品推荐"(T2)，返回 recommendation.matches。
本脚本只是把每条的真实路由结果打印出来供人工判断，不做硬性断言（依赖真模型表现）。

用法（在服务器/能连到 API 的机器）：
  E2E_BASE=http://127.0.0.1:8000/api/v1 PYTHONPATH=. python scripts/probe_llm_fallback_gain.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8010/api/v1")
CUSTOMER = (os.environ.get("E2E_USER", "customer_demo"), os.environ.get("E2E_PASS", "Demo@2026Customer"))

# 候选"规则未覆盖的推荐意图"问句（尽量避开 推荐/适合我的产品/能买/申购 等规则命中词）
PROBES = [
    "给点适合我这种上班族的低门槛选择",
    "帮我挑几个稳一点的",
    "有没有适合刚工作的新手的产品",
    "想找那种存进去不操心、还能有点收益的",
    "你们有什么打理闲钱的方式",
]


def _walk(node, key):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            out.extend(_walk(v, key))
    elif isinstance(node, list):
        for it in node:
            out.extend(_walk(it, key))
    return out


def _has_recommendation(data):
    for lst in _walk(data, "matches"):
        if isinstance(lst, list) and lst:
            return True
    note = _walk(data, "preference_note")
    return bool(note and isinstance(note[0], str) and note[0])


async def probe(client, token, conv, q):
    r = await client.post(
        f"{BASE}/customer-service/sessions/agent/execute",
        headers={"Authorization": f"Bearer {token}"},
        json={"content": q, "conversation_id": conv, "page_context": "/customer-center"},
    )
    if r.status_code != 200:
        return q, "HTTP " + str(r.status_code), None
    d = r.json().get("data") or {}
    status = d.get("overall_status")
    rec = _has_recommendation(d)
    answer = (d.get("answer") or "")[:60]
    return q, status, rec, answer


async def main() -> int:
    conv = f"fbprobe-{uuid.uuid4()}"
    async with httpx.AsyncClient(timeout=120) as client:
        login = await client.post(f"{BASE}/auth/login", json={"username": CUSTOMER[0], "password": CUSTOMER[1]})
        if login.status_code != 200:
            print("login failed:", login.text[:300], file=sys.stderr)
            return 2
        token = login.json()["data"]["access_token"]
        lifted = 0
        for q in PROBES:
            q, status, rec, answer = await probe(client, token, conv, q)
            flag = "✅推荐(被提升)" if rec else "⚠️非推荐"
            if rec:
                lifted += 1
            print(f"[{flag}] {q}\n    status={status} answer={answer!r}")
        print(f"\n共 {len(PROBES)} 条，其中被提升为推荐的 {lifted} 条。")
        print("若多数被提升 → 非交易 LLM 兜底增益生效。若多为⚠️，回传输出我再判断是模型未识别还是仍被规则拦截。")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
