from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import redis.asyncio as redis
from redis.exceptions import ResponseError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.settings import get_settings
from app.services.permission_scoped_cache import bump_subject_scope_version
from app.tasks.streams import RedisStreams
from workers.authorization_worker import handle_message


async def main() -> None:
    settings = get_settings()
    client = redis.from_url(settings.redis_url, decode_responses=True)
    queue = RedisStreams(settings)
    stream = f"{settings.redis_stream_prefix}:authorization:test:{uuid.uuid4().hex[:10]}"
    group = f"authorization-retry-test-{uuid.uuid4().hex[:8]}"
    consumer_a = "retry-consumer-a"
    consumer_b = "retry-consumer-b"
    advisor_id = f"retry-advisor-{uuid.uuid4().hex[:8]}"
    checks: dict[str, bool] = {}
    try:
        event = {
            "event_type": "authorization.subject_scope_invalidate",
            "event_id": str(uuid.uuid4()),
            "advisor_id": advisor_id,
        }
        message_id = await client.xadd(stream, event)
        await client.xgroup_create(stream, group, id="0", mkstream=True)
        first = await client.xreadgroup(group, consumer_a, {stream: ">"}, count=1)
        checks["message_delivered_to_first_consumer"] = bool(first)
        first_message_id, first_data = first[0][1][0]
        # Inject handler failure before ACK: the message must remain pending.
        pending_before = await client.xpending_range(stream, group, min="-", max="+", count=10)
        checks["message_pending_after_unacked_failure"] = any(item["message_id"] == first_message_id for item in pending_before)
        claimed = await client.xclaim(stream, group, consumer_b, min_idle_time=0, message_ids=[first_message_id])
        checks["pending_message_claimed_by_second_consumer"] = bool(claimed and claimed[0][0] == first_message_id)
        if claimed:
            claimed_id, claimed_data = claimed[0]
            class Message:
                message_id = claimed_id
                data = claimed_data
            result = await handle_message(queue, Message())
            checks["claimed_message_processed"] = result == "1"
            checks["claimed_message_acked"] = await client.xack(stream, group, claimed_id) == 1
        version = await client.get(f"wm:subject-scope-version:{advisor_id}")
        checks["scope_version_incremented_after_claim"] = str(version) == "1"
        pending_after = await client.xpending_range(stream, group, min="-", max="+", count=10)
        checks["no_pending_after_ack"] = not any(item["message_id"] == message_id for item in pending_after)
        failed = [name for name, passed in checks.items() if not passed]
        print(json.dumps({"checks": checks, "pass": not failed, "failed": failed}, ensure_ascii=False))
        if failed:
            raise SystemExit(1)
    finally:
        await client.delete(stream, f"wm:subject-scope-version:{advisor_id}")
        await queue.close()
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
