#!/usr/bin/env python
"""记忆管线 E2E：多轮对话 → outbox 入队 → LLM 提炼摘要 → 共享记忆 → 投顾召回。

依赖运行中的 API（本脚本用 HTTP 走真实链路）+ 本机 Postgres/Redis/DASHSCOPE。
用法：先起 API，再 `PYTHONPATH=. python scripts/test_memory_pipeline_e2e.py`。
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import httpx
from sqlalchemy import select, delete

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.settings import get_settings
from app.db.session import Database
from app.models.outbox import OutboxEvent
from app.models.auth import User
from app.models.memory import CustomerMemory, CustomerMemoryEvent
from app.services.conversation_summary import ConversationSummaryService
from app.services.shared_memory_service import SharedMemoryService

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8098/api/v1")


async def main() -> None:
    db = Database(get_settings())
    conv = f"e2e-{uuid.uuid4()}"
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            login = await client.post(f"{BASE}/auth/login", json={"username": "customer_demo", "password": "Demo@2026Customer"})
            login.raise_for_status()
            token = login.json()["data"]["access_token"]
            h = {"Authorization": f"Bearer {token}"}
            for i, content in enumerate(["帮我推荐稳健的产品", "我偏稳健，长期持有", "那这个收益怎么样"], 1):
                r = await client.post(f"{BASE}/customer-service/sessions/agent/execute", headers=h,
                                      json={"content": content, "conversation_id": conv, "page_context": "/customer-center"})
                r.raise_for_status()
                print(f"turn{i} ok status={r.json()['data']['overall_status']}")

        # 1) outbox 已入队 memory.summary_extract
        async with db.session_factory() as session:
            events = list((await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "memory.summary_extract"))).scalars().all())
            print("outbox memory.summary_extract events:", len(events))
            assert events, "no summary events enqueued"

        # 2) 模拟 memory_worker：用真 LLM 逐条提炼落库
        svc = ConversationSummaryService(db)
        stored = 0
        async with db.session_factory() as session:
            for ev in events:
                p = ev.payload_json
                cid = p.get("customer_id")
                if not cid or not (await session.execute(select(User).where(User.id == cid))).scalar_one_or_none():
                    continue
                context = {
                    "original_query": p.get("original_query", ""),
                    "latest_query": p.get("latest_query", ""),
                    "task_results": p.get("task_results", []),
                    "active_product": p.get("active_product", ""),
                    "handoff_reason": p.get("handoff_reason", ""),
                }
                if await svc.summarize_and_store(customer_id=cid, conversation_id=p.get("conversation_id", ""), context=context, actor_user_id=cid):
                    stored += 1
        print("stored summaries:", stored)
        assert stored >= 1, "no summary stored"

        # 3) 投顾可召回共享摘要
        async with db.session_factory() as session:
            u = (await session.execute(select(User).where(User.username == "customer_demo"))).scalar_one()
        recalled = await SharedMemoryService(db).recall(customer_id=str(u.id), agent_type="advisor", key="conversation_summary")
        print("advisor recalls summary:", len(recalled))
        assert recalled, "advisor cannot recall summary"
        print("E2E SUMMARY:", recalled[0].value[:200])

        # 4) 后续一轮仍能正常执行（execute() 会加载摘要上下文，不报错）
        async with httpx.AsyncClient(timeout=90) as client:
            login = await client.post(f"{BASE}/auth/login", json={"username": "customer_demo", "password": "Demo@2026Customer"})
            token = login.json()["data"]["access_token"]
            r = await client.post(f"{BASE}/customer-service/sessions/agent/execute", headers={"Authorization": f"Bearer {token}"},
                                  json={"content": "帮我看看我的资产", "conversation_id": conv, "page_context": "/customer-center"})
            r.raise_for_status()
            print("follow-up turn ok status=", r.json()["data"]["overall_status"])

        print("MEMORY PIPELINE E2E PASS")
    finally:
        async with db.session_factory() as session:
            u = (await session.execute(select(User).where(User.username == "customer_demo"))).scalar_one_or_none()
            if u:
                await session.execute(delete(CustomerMemoryEvent).where(CustomerMemoryEvent.customer_id == str(u.id)))
                await session.execute(delete(CustomerMemory).where(CustomerMemory.customer_id == str(u.id)))
            await session.execute(delete(OutboxEvent).where(OutboxEvent.event_type == "memory.summary_extract"))
            await session.commit()
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
