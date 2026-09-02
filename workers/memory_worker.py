from __future__ import annotations

"""记忆 Worker：对话中后台异步提炼情景记忆（L3 稳定摘要）。

消费 outbox 发布的 `memory.summary_extract`（Redis `:memory` stream），
加载已有 `conversation_summary` + 本轮上下文 → LLM 递进提炼 → 落共享记忆。
"""
import asyncio

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.services.conversation_summary import ConversationSummaryService
from app.services.shared_memory_service import SharedMemoryService
from app.tasks.streams import RedisStreams

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    queue = RedisStreams(settings)
    stream = f"{settings.redis_stream_prefix}:memory"
    group = f"{settings.redis_consumer_group}-memory"
    consumer = f"{settings.redis_consumer_name}-memory"
    await queue.ensure_group(stream, group)
    service = ConversationSummaryService(database)
    shared = SharedMemoryService(database)
    logger.info("memory worker started stream=%s group=%s consumer=%s", stream, group, consumer)
    try:
        while True:
            messages = await queue.claim_pending(stream, group, consumer)
            messages.extend(await queue.consume_once(stream, group, consumer))
            for message in messages:
                try:
                    customer_id = message.data.get("customer_id", "")
                    conversation_id = message.data.get("conversation_id", "")
                    if not customer_id:
                        continue
                    existing = ""
                    try:
                        rows = await shared.recall(customer_id=customer_id, agent_type="customer_service", key="conversation_summary")
                        if rows:
                            existing = str(rows[0].value)
                    except Exception:  # noqa: BLE001
                        existing = ""
                    context = {
                        "original_query": message.data.get("original_query", ""),
                        "latest_query": message.data.get("latest_query", ""),
                        "task_results": message.data.get("task_results", []),
                        "active_product": message.data.get("active_product", ""),
                        "handoff_reason": message.data.get("handoff_reason", ""),
                        "existing_summary": existing,
                    }
                    await service.summarize_and_store(
                        customer_id=customer_id, conversation_id=conversation_id,
                        context=context, actor_user_id=customer_id)
                except Exception:  # noqa: BLE001
                    logger.exception("memory summary failed message_id=%s", message.message_id)
                finally:
                    await queue.ack(message)
    finally:
        await queue.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
