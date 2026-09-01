from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.services.customer_service_timeout import CustomerServiceTimeoutService

logger = get_logger(__name__)


async def run_timeout_sweep(database: Database, service: CustomerServiceTimeoutService, settings, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    async with database.session_factory() as session:
        data = await service.sweep(
            session,
            timeout_seconds=settings.customer_service_heartbeat_timeout_seconds,
            waiting_timeout_seconds=settings.customer_service_waiting_timeout_seconds,
            cleanup_waiting=settings.customer_service_waiting_cleanup_enabled,
            actor_user_id=None,
            now=now,
        )
    logger.info(
        "customer_service_timeout_sweep paused_count=%s expired_count=%s timeout_seconds=%s waiting_timeout_seconds=%s",
        data["paused_count"], data["expired_count"], data["timeout_seconds"], data["waiting_timeout_seconds"],
    )
    return data


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.customer_service_timeout_worker_enabled:
        logger.warning("customer service timeout worker is disabled")
        return
    database = Database(settings)
    service = CustomerServiceTimeoutService()
    logger.info(
        "customer service timeout worker started poll_seconds=%s timeout_seconds=%s",
        settings.customer_service_timeout_worker_poll_seconds,
        settings.customer_service_heartbeat_timeout_seconds,
    )
    try:
        while True:
            try:
                await run_timeout_sweep(database, service, settings)
            except Exception:
                logger.exception("customer service timeout sweep failed")
            await asyncio.sleep(settings.customer_service_timeout_worker_poll_seconds)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
