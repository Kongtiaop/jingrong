from __future__ import annotations

import asyncio
import json

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.services.permission_scoped_cache import bump_subject_scope_version
from app.tasks.streams import RedisStreams

logger = get_logger(__name__)


def decode_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        decoded = json.loads(value)
        return str(decoded)
    except (TypeError, json.JSONDecodeError):
        return str(value)


async def handle_message(queue: RedisStreams, message) -> str:
    """Handle one message and return the resulting scope version."""
    if message.data.get("event_type") != "authorization.subject_scope_invalidate":
        logger.warning("authorization worker ignored unexpected event_type=%s message_id=%s", message.data.get("event_type"), message.message_id)
        return "ignored"
    advisor_id = decode_value(message.data.get("advisor_id"))
    if not advisor_id:
        raise ValueError("authorization invalidation requires advisor_id")
    version = await bump_subject_scope_version(queue.client, user_id=advisor_id)
    logger.info("authorization_scope_invalidated event_id=%s advisor_id=%s version=%s", message.data.get("event_id"), advisor_id, version)
    return version


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    queue = RedisStreams(settings)
    stream = f"{settings.redis_stream_prefix}:authorization"
    group = f"{settings.redis_consumer_group}-authorization"
    consumer = f"{settings.redis_consumer_name}-authorization"
    await queue.ensure_group(stream, group)
    logger.info("authorization worker started stream=%s group=%s consumer=%s", stream, group, consumer)
    try:
        while True:
            messages = await queue.claim_pending(stream, group, consumer)
            messages.extend(await queue.consume_once(stream, group, consumer))
            for message in messages:
                try:
                    await handle_message(queue, message)
                    await queue.ack(message)
                except Exception:
                    logger.exception("authorization event handling failed message_id=%s; leaving pending for claim retry", message.message_id)
    finally:
        await queue.close()


if __name__ == "__main__":
    asyncio.run(main())
