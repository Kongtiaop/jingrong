from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8010/api/v1"
CUSTOMER = {"username": "customer_demo", "password": "Demo@2026Customer"}
AGENT = {"username": "customer_service_demo", "password": "Demo@2026CustomerService"}


async def login(client: httpx.AsyncClient, account: dict[str, str]) -> dict[str, str]:
    response = await client.post("/auth/login", json=account)
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def unread_for_session(client: httpx.AsyncClient, headers: dict[str, str], session_id: str, notification_type: str) -> list[dict]:
    response = await client.get("/notifications", params={"unread_only": "true", "limit": 100}, headers=headers)
    response.raise_for_status()
    return [item for item in response.json()["data"]["items"] if item["resource_id"] == session_id and item["notification_type"] == notification_type]


async def wait_for_notification(client: httpx.AsyncClient, headers: dict[str, str], session_id: str, notification_type: str) -> list[dict]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        rows = await unread_for_session(client, headers, session_id, notification_type)
        if rows:
            return rows
        await asyncio.sleep(0.5)
    return []


async def main() -> None:
    checks: dict[str, bool] = {}
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
        customer = await login(client, CUSTOMER)
        agent = await login(client, AGENT)
        created = await client.post("/customer-service/sessions", headers=customer, json={"note": f"notification_outbox_{uuid.uuid4()}"})
        created.raise_for_status()
        session_id = created.json()["data"]["id"]
        try:
            claim = await client.post(f"/customer-service/sessions/{session_id}/claim", headers=agent)
            checks["claim_succeeds"] = claim.status_code == 200
            claim_notifications = await wait_for_notification(client, customer, session_id, "customer_service_claimed")
            checks["claim_notification_delivered"] = len(claim_notifications) == 1

            message = await client.post(f"/customer-service/sessions/{session_id}/messages", headers=customer, json={"content": "Outbox 异步通知回归消息"})
            checks["customer_message_succeeds"] = message.status_code == 201
            message_notifications = await wait_for_notification(client, agent, session_id, "customer_service_message")
            checks["message_notification_delivered"] = len(message_notifications) == 1

            repeat = await client.post(f"/customer-service/sessions/{session_id}/messages", headers=customer, json={"content": "Outbox 异步通知回归消息 2"})
            checks["second_message_succeeds"] = repeat.status_code == 201
            deadline = time.monotonic() + 15
            rows: list[dict] = []
            while time.monotonic() < deadline:
                rows = await unread_for_session(client, agent, session_id, "customer_service_message")
                if len(rows) >= 2:
                    break
                await asyncio.sleep(0.5)
            checks["distinct_messages_create_distinct_notifications"] = len(rows) == 2
            checks["no_duplicate_per_event"] = len({row["id"] for row in rows}) == len(rows)
        finally:
            await client.post(f"/customer-service/sessions/{session_id}/resolve", headers=agent, json={"note": "outbox regression cleanup"})
    failed = [name for name, passed in checks.items() if not passed]
    print(json.dumps({"checks": checks, "pass": not failed, "failed": failed}, ensure_ascii=False, indent=2))
    if failed:
        raise AssertionError(failed)


if __name__ == "__main__":
    asyncio.run(main())
