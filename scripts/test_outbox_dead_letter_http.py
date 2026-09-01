from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.settings import get_settings
from app.db.session import Database
from app.models.outbox import OutboxEvent
from workers.outbox_worker import OutboxPublisher

BASE = "http://127.0.0.1:8010/api/v1"


class FailingQueue:
    async def publish(self, stream, data):
        raise RuntimeError("forced_outbox_publish_failure")


async def login(client, username, password):
    response = await client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


async def main():
    settings = get_settings()
    database = Database(settings)
    event_id = str(uuid.uuid4())
    checks = {}
    try:
        async with database.session_factory() as session:
            session.add(OutboxEvent(
                id=event_id,
                event_type="notification.customer_service",
                aggregate_type="customer_service_session",
                aggregate_id=str(uuid.uuid4()),
                payload_json={
                    "user_id": "dead-letter-test",
                    "notification_type": "customer_service_message",
                    "title": "dead letter test",
                    "content": "forced failure",
                    "resource_type": "customer_service_session",
                    "resource_id": str(uuid.uuid4()),
                    "idempotency_key": f"dead-letter-test:{event_id}",
                },
                status="pending",
            ))
            await session.commit()

        publisher = OutboxPublisher(database, FailingQueue(), settings)
        for _ in range(settings.task_max_attempts):
            await publisher.publish_once(limit=10)
        async with database.session_factory() as session:
            row = await session.get(OutboxEvent, event_id)
            checks["event_becomes_dead"] = row is not None and row.status == "dead" and row.attempt == settings.task_max_attempts and "forced_outbox_publish_failure" in (row.error_message or "")

        async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
            admin = await login(client, "super_admin_demo", "Demo@2026SuperAdmin")
            customer = await login(client, "customer_demo", "Demo@2026Customer")
            agent = await login(client, "customer_service_demo", "Demo@2026CustomerService")
            listed = await client.get("/admin/outbox-events", params={"status": "dead", "limit": 100}, headers=admin)
            checks["admin_lists_dead"] = listed.status_code == 200 and any(item["id"] == event_id for item in listed.json()["data"]["items"])
            forbidden = await client.get("/admin/outbox-events", headers=customer)
            checks["customer_cannot_list"] = forbidden.status_code == 403
            retry = await client.post(f"/admin/outbox-events/{event_id}/retry", headers=admin)
            checks["admin_retries_dead"] = retry.status_code == 200 and retry.json()["data"]["status"] == "pending" and retry.json()["data"]["attempt"] == 0
            repeated = await client.post(f"/admin/outbox-events/{event_id}/retry", headers=admin)
            checks["only_dead_can_retry"] = repeated.status_code == 409

            delivery_session = await client.post("/customer-service/sessions", headers=customer, json={"note": f"dead_retry_delivery_{uuid.uuid4()}"})
            delivery_session.raise_for_status()
            delivery_session_id = delivery_session.json()["data"]["id"]
            delivery_claim = await client.post(f"/customer-service/sessions/{delivery_session_id}/claim", headers=agent)
            checks["delivery_claim_succeeds"] = delivery_claim.status_code == 200
            deadline = asyncio.get_running_loop().time() + 15
            delivered = []
            while asyncio.get_running_loop().time() < deadline:
                response = await client.get("/notifications", params={"unread_only": "true", "limit": 100}, headers=customer)
                response.raise_for_status()
                delivered = [item for item in response.json()["data"]["items"] if item["resource_id"] == delivery_session_id and item["notification_type"] == "customer_service_claimed"]
                if delivered:
                    break
                await asyncio.sleep(0.5)
            checks["real_workers_deliver_after_retry_path"] = len(delivered) == 1
            await client.post(f"/customer-service/sessions/{delivery_session_id}/resolve", headers=agent, json={"note": "dead retry delivery cleanup"})

        failed = [name for name, passed in checks.items() if not passed]
        print(json.dumps({"checks": checks, "pass": not failed, "failed": failed}, ensure_ascii=False, indent=2))
        if failed:
            raise AssertionError(failed)
    finally:
        async with database.session_factory() as session:
            await session.execute(text("DELETE FROM audit_logs WHERE resource_type='outbox_event' AND resource_id=:id"), {"id": event_id})
            await session.execute(text("DELETE FROM outbox_event WHERE id=:id"), {"id": event_id})
            await session.commit()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
