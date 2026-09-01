from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8010/api/v1"
WS_BASE = "ws://127.0.0.1:8010/api/v1"


async def login(client, username: str, password: str) -> str:
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return response.json()["data"]["access_token"]


async def receive_until(socket, request_id: str, terminal_types: set[str], timeout: float = 30.0):
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(socket.recv(), timeout))
        events.append(event)
        if event.get("request_id") == request_id and event.get("type") in terminal_types:
            return events


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        token = await login(client, "customer_demo", "Demo@2026Customer")

    conversation_id = f"ws-e2e-{uuid.uuid4()}"
    uri = f"{WS_BASE}/customer-service/sessions/ws/{conversation_id}?access_token={token}"
    async with websockets.connect(uri, open_timeout=10, close_timeout=10) as socket:
        ready = json.loads(await asyncio.wait_for(socket.recv(), 10))

        request_id = str(uuid.uuid4())
        await socket.send(json.dumps({
            "type": "agent.request",
            "request_id": request_id,
            "content": "帮我查订单状态、账户风险限制和资产情况",
        }, ensure_ascii=False))
        normal_events = await receive_until(socket, request_id, {"agent.final", "agent.error"})

        amend_id = str(uuid.uuid4())
        await socket.send(json.dumps({
            "type": "agent.amend",
            "request_id": amend_id,
            "content": "补充查询资产情况",
        }, ensure_ascii=False))
        amend_events = await receive_until(socket, amend_id, {"agent.final", "agent.error"})

        cancel_id = str(uuid.uuid4())
        await socket.send(json.dumps({
            "type": "agent.request",
            "request_id": cancel_id,
            "content": "[cancel-test] 请查询订单状态并取消本次请求",
        }, ensure_ascii=False))
        await asyncio.sleep(0.2)
        await socket.send(json.dumps({"type": "agent.cancel", "request_id": cancel_id}))
        cancel_events = await receive_until(socket, cancel_id, {"agent.cancelled", "agent.final", "agent.error"})

    normal_types = [event["type"] for event in normal_events]
    amend_types = [event["type"] for event in amend_events]
    cancel_types = [event["type"] for event in cancel_events]
    normal_final = next((event for event in normal_events if event["type"] == "agent.final"), None)
    checks = {
        "session_ready": ready.get("type") == "session.ready",
        "normal_final": "agent.final" in normal_types,
        "normal_chunks": "agent.output.chunk" in normal_types,
        "normal_structured_result": bool(normal_final and normal_final.get("payload", {}).get("result", {}).get("results") is not None),
        "amend_final": "agent.final" in amend_types,
        "cancelled": "agent.cancelled" in cancel_types,
        "normal_sequence_monotonic": [event.get("sequence") for event in normal_events] == sorted(event.get("sequence") for event in normal_events),
        "conversation_consistent": all(event.get("conversation_id") == conversation_id for event in normal_events + amend_events + cancel_events),
    }
    failed = [key for key, value in checks.items() if not value]
    print(json.dumps({
        "checks": checks,
        "normal_event_types": normal_types,
        "amend_event_types": amend_types,
        "cancel_event_types": cancel_types,
        "pass": not failed,
        "failed": failed,
    }, ensure_ascii=False, indent=2))
    if failed:
        raise AssertionError(failed)


if __name__ == "__main__":
    asyncio.run(main())
