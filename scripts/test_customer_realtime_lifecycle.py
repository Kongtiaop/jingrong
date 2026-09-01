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
CUSTOMER = {"username": "customer_demo", "password": "Demo@2026Customer"}
AGENT = {"username": "customer_service_e2e", "password": "Demo@2026CustomerService"}


async def login(client, account: dict[str, str]) -> str:
    response = await client.post("/auth/login", json=account)
    response.raise_for_status()
    return response.json()["data"]["access_token"]


async def request(client, method: str, path: str, token: str, payload: dict | None = None):
    response = await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, json=payload)
    return response


async def release_existing_active_sessions(client, agent_token: str) -> None:
    response = await client.get("/customer-service/sessions", headers={"Authorization": f"Bearer {agent_token}"}, params={"status": "human_active"})
    response.raise_for_status()
    for row in response.json()["data"]:
        await request(client, "POST", f"/customer-service/sessions/{row['id']}/release", agent_token, {"note": "lifecycle_regression_cleanup"})


async def receive_until(socket, predicate, timeout: float = 30.0):
    events = []
    while True:
        event = json.loads(await asyncio.wait_for(socket.recv(), timeout))
        events.append(event)
        if predicate(event):
            return events


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        customer_token = await login(client, CUSTOMER)
        agent_token = await login(client, AGENT)
        await release_existing_active_sessions(client, agent_token)
        created = await request(client, "POST", "/customer-service/sessions", customer_token, {"note": "websocket lifecycle regression"})
        created.raise_for_status()
        session_id = created.json()["data"]["id"]

        conversation_id = f"ws-lifecycle-{uuid.uuid4()}"
        uri = f"{WS_BASE}/customer-service/sessions/ws/{conversation_id}?access_token={customer_token}"
        try:
            async with websockets.connect(uri, open_timeout=10, close_timeout=10) as socket:
                ready = json.loads(await asyncio.wait_for(socket.recv(), 10))
                await asyncio.sleep(0.1)
                claim = await request(client, "POST", f"/customer-service/sessions/{session_id}/claim", agent_token)
                claim.raise_for_status()
                claimed_events = await receive_until(socket, lambda event: event.get("type") == "human.session.claimed" and event.get("payload", {}).get("session_id") == session_id)

                paused = await request(client, "POST", f"/customer-service/sessions/{session_id}/pause", agent_token, {"note": "lifecycle regression"})
                paused.raise_for_status()
                paused_events = await receive_until(socket, lambda event: event.get("type") == "human.session.paused" and event.get("payload", {}).get("session_id") == session_id)

                resolved = await request(client, "POST", f"/customer-service/sessions/{session_id}/resolve", agent_token, {"note": "lifecycle regression complete"})
                resolved.raise_for_status()
                resolved_events = await receive_until(socket, lambda event: event.get("type") == "human.session.resolved" and event.get("payload", {}).get("session_id") == session_id)

                closed = await request(client, "POST", f"/customer-service/sessions/{session_id}/close", agent_token)
                closed.raise_for_status()
                closed_events = await receive_until(socket, lambda event: event.get("type") == "human.session.closed" and event.get("payload", {}).get("session_id") == session_id)
        finally:
            await request(client, "POST", f"/customer-service/sessions/{session_id}/customer-close", customer_token, {"note": "lifecycle_regression_cleanup"})

    checks = {
        "session_ready": ready.get("type") == "session.ready",
        "claimed": any(event.get("type") == "human.session.claimed" for event in claimed_events),
        "paused": any(event.get("type") == "human.session.paused" for event in paused_events),
        "resolved": any(event.get("type") == "human.session.resolved" for event in resolved_events),
        "closed": any(event.get("type") == "human.session.closed" for event in closed_events),
    }
    failed = [key for key, value in checks.items() if not value]
    print(json.dumps({"checks": checks, "pass": not failed, "failed": failed}, ensure_ascii=False, indent=2))
    if failed:
        raise AssertionError(failed)


if __name__ == "__main__":
    asyncio.run(main())
