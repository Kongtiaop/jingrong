from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.models.trading import AmlSchedulerState
from app.repositories.unit_of_work import create_postgres_unit_of_work
from app.services.daily_aml_scan_service import DailyAmlScanService

logger = get_logger(__name__)


def due_now(now: datetime, settings) -> bool:
    return now.hour == settings.aml_scheduler_hour_utc and now.minute == settings.aml_scheduler_minute_utc


async def run_due_scan(database: Database, service: DailyAmlScanService, settings, now: datetime) -> bool:
    async with database.session_factory() as session:
        run, created = await service.run(session, as_of=now, days=settings.aml_scheduler_days)
        await session.commit()
    logger.info(
        "aml_scheduler_scan batch_id=%s created=%s status=%s customers=%s created_alerts=%s",
        run.id, created, run.status, run.total_customers, run.created_alerts,
    )
    return created


async def record_state(database: Database, now: datetime, *, started: bool = False, finished: bool = False, status: str | None = None, error: str = "") -> None:
    async with database.session_factory() as session:
        state = await session.get(AmlSchedulerState, "daily_aml", with_for_update=True)
        if state is None:
            state = AmlSchedulerState(id="daily_aml", last_heartbeat_at=now)
            session.add(state)
        state.last_heartbeat_at = now
        if started:
            state.last_scan_started_at = now
        if finished:
            state.last_scan_finished_at = now
        if status is not None:
            state.last_scan_status = status
            state.last_error = error[:2000]
        await session.commit()


async def run_scheduler_cycle(database: Database, service: DailyAmlScanService, settings, now: datetime, last_slot: str) -> str:
    """Record a heartbeat and run at most once for a UTC minute slot."""
    slot = now.strftime("%Y-%m-%dT%H:%M")
    await record_state(database, now)
    if not due_now(now, settings) or slot == last_slot:
        return last_slot
    try:
        await record_state(database, now, started=True, status="running")
        await run_due_scan(database, service, settings, now)
    except Exception:
        logger.exception("aml scheduler scan failed slot=%s", slot)
        await record_state(database, now, finished=True, status="failed", error="scan failed; see scheduler logs")
        return last_slot
    await record_state(database, now, finished=True, status="completed")
    return slot


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.aml_scheduler_enabled:
        logger.warning("aml scheduler is disabled; set AML_SCHEDULER_ENABLED=true to enable it")
        return

    database = Database(settings)
    service = DailyAmlScanService(create_postgres_unit_of_work())
    last_slot = ""
    logger.info(
        "aml scheduler started hour_utc=%s minute_utc=%s days=%s poll_seconds=%s",
        settings.aml_scheduler_hour_utc,
        settings.aml_scheduler_minute_utc,
        settings.aml_scheduler_days,
        settings.aml_scheduler_poll_seconds,
    )
    try:
        while True:
            now = datetime.now(timezone.utc)
            last_slot = await run_scheduler_cycle(database, service, settings, now, last_slot)
            await asyncio.sleep(settings.aml_scheduler_poll_seconds)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
