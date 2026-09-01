from __future__ import annotations

import json


async def publish_customer_realtime(redis_client, customer_id: str, event_type: str, session_id: str, payload: dict | None = None) -> None:
    event = {"type": event_type, "customer_id": customer_id, "session_id": session_id, "payload": payload or {}}
    await redis_client.publish(f"customer-realtime:{customer_id}", json.dumps(event, ensure_ascii=False))
