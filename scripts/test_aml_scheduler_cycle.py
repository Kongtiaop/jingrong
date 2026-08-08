from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from workers.aml_scheduler import run_scheduler_cycle


async def main() -> None:
    settings = SimpleNamespace(aml_scheduler_hour_utc=1, aml_scheduler_minute_utc=10)
    due = datetime(2026, 8, 5, 1, 10, 25, tzinfo=timezone.utc)
    later = datetime(2026, 8, 5, 1, 11, 0, tzinfo=timezone.utc)
    fake_scan = AsyncMock(return_value=True)
    with patch("workers.aml_scheduler.record_state", AsyncMock()), patch("workers.aml_scheduler.run_due_scan", fake_scan):
        slot = await run_scheduler_cycle(None, None, settings, due, "")
        assert slot == "2026-08-05T01:10"
        assert fake_scan.await_count == 1
        same_slot = await run_scheduler_cycle(None, None, settings, due, slot)
        assert same_slot == slot and fake_scan.await_count == 1
        not_due = await run_scheduler_cycle(None, None, settings, later, slot)
        assert not_due == slot and fake_scan.await_count == 1
    failed_scan = AsyncMock(side_effect=RuntimeError("database unavailable"))
    with patch("workers.aml_scheduler.record_state", AsyncMock()), patch("workers.aml_scheduler.run_due_scan", failed_scan):
        retry_slot = await run_scheduler_cycle(None, None, settings, due, "")
        assert retry_slot == "" and failed_scan.await_count == 1
    print("{\"due_slot_runs_once\": true, \"same_slot_deduped\": true, \"failure_retries_next_poll\": true, \"pass\": true}")


if __name__ == "__main__":
    asyncio.run(main())
