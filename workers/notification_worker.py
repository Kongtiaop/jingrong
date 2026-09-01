from __future__ import annotations

import asyncio

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.services.notification_delivery import NotificationDeliveryService
from app.tasks.streams import RedisStreams


logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    queue = RedisStreams(settings)
    stream = f"{settings.redis_stream_prefix}:notifications"
    group = f"{settings.redis_consumer_group}-notifications"
    consumer = f"{settings.redis_consumer_name}-notifications"
    service = NotificationDeliveryService(database)
    await queue.ensure_group(stream, group)
    logger.info("notification worker started stream=%s group=%s consumer=%s", stream, group, consumer)
    try:
        while True:
            messages = await queue.claim_pending(stream, group, consumer)
            messages.extend(await queue.consume_once(stream, group, consumer))
            for message in messages:
                try:
                    if message.data.get("event_type") != "notification.customer_service":
                        logger.warning("notification worker ignored unexpected event_type=%s message_id=%s", message.data.get("event_type"), message.message_id)
                    else:
                        delivered = await service.deliver_customer_service(message.data)
                        logger.info("notification_delivery_completed event_id=%s delivered=%s", message.data.get("event_id"), delivered)
                    await queue.ack(message)
                except Exception:
                    logger.exception("notification delivery failed message_id=%s", message.message_id)
    finally:
        await queue.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
