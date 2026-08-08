from __future__ import annotations

import asyncio
from time import perf_counter

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.repositories.data_catalog import DataCatalogRepository
from app.services.data_catalog_embedding import DataCatalogEmbeddingService
from app.tasks.streams import RedisStreams


logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    queue = RedisStreams(settings)
    stream = f"{settings.redis_stream_prefix}:catalog"
    group = f"{settings.redis_consumer_group}-catalog"
    consumer = f"{settings.redis_consumer_name}-catalog"
    await queue.ensure_group(stream, group)
    provider = QwenProvider(settings)
    service = DataCatalogEmbeddingService(DataCatalogRepository(database), provider)
    logger.info("data catalog worker started stream=%s group=%s consumer=%s", stream, group, consumer)
    try:
        while True:
            messages = await queue.claim_pending(stream, group, consumer)
            messages.extend(await queue.consume_once(stream, group, consumer))
            for message in messages:
                try:
                    started = perf_counter()
                    limit = int(message.data.get("limit", "100") or 100)
                    result = await service.embed_pending(limit=min(max(limit, 1), 500))
                    logger.info(
                        "data_catalog_embedding_completed tables=%s columns=%s examples=%s updated=%s duration_ms=%.2f",
                        result.tables,
                        result.columns,
                        result.examples,
                        result.updated,
                        (perf_counter() - started) * 1000,
                    )
                except Exception:
                    logger.exception("data catalog embedding failed message_id=%s", message.message_id)
                finally:
                    await queue.ack(message)
    finally:
        await provider.close()
        await queue.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
