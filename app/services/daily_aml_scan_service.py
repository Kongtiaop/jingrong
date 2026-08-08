from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.auth import User
from app.models.trading import AmlBatchAlert, AmlBatchRun
from app.services.risk_alert_service import RiskAlertService


class DailyAmlScanService:
    def __init__(self, unit_of_work):
        self.uow = unit_of_work

    @staticmethod
    def window_for(as_of: datetime, days: int) -> tuple[datetime, datetime]:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        utc_as_of = as_of.astimezone(timezone.utc)
        window_end = utc_as_of.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return window_end - timedelta(days=days), window_end

    @staticmethod
    def batch_key(window_start: datetime, window_end: datetime, days: int, username: str = "") -> str:
        scope = username or "all_customers"
        return f"daily_aml:{scope}:{days}:{window_start.isoformat()}:{window_end.isoformat()}"

    async def run(self, session, as_of: datetime | None = None, days: int = 7, username: str = "", key_suffix: str = "", run_type: str = "daily", source_batch_id: str | None = None) -> tuple[AmlBatchRun, bool]:
        as_of = as_of or datetime.now(timezone.utc)
        window_start, window_end = self.window_for(as_of, days)
        key = self.batch_key(window_start, window_end, days, username) + (f":{key_suffix}" if key_suffix else "")
        existing = (await session.execute(select(AmlBatchRun).where(AmlBatchRun.batch_key == key))).scalar_one_or_none()
        if existing is not None:
            return existing, False

        run = AmlBatchRun(
            id=str(uuid4()), batch_key=key, scan_window_start=window_start, scan_window_end=window_end,
            scan_days=days, status="running", started_at=datetime.now(timezone.utc), run_type=run_type, source_batch_id=source_batch_id,
        )
        session.add(run)
        await session.flush()

        query = select(User).where(User.status == "active", User.roles.any(code="customer")).order_by(User.username)
        if username:
            query = query.where(User.username == username)
        customers = list((await session.execute(query)).scalars().all())
        if username and not customers:
            run.status = "failed"
            run.error_summary = f"customer username not found: {username}"
            run.finished_at = datetime.now(timezone.utc)
            await session.flush()
            return run, True

        run.total_customers = len(customers)
        scanner = RiskAlertService(self.uow)
        errors: list[dict[str, str]] = []
        for customer in customers:
            try:
                async with session.begin_nested():
                    alerts = await scanner.scan_customer_window(session, customer.id, as_of=as_of, days=days)
                run.succeeded_customers += 1
                run.created_alerts += len(alerts)
                for alert in alerts:
                    session.add(AmlBatchAlert(batch_id=run.id, alert_id=alert.id))
            except Exception as exc:  # keep a single malformed customer from aborting the batch
                run.failed_customers += 1
                errors.append({"user_id": customer.id, "username": customer.username, "error": str(exc)[:300]})

        run.status = "completed" if not errors else "completed_with_errors"
        run.error_summary = json.dumps(errors, ensure_ascii=False) if errors else ""
        run.finished_at = datetime.now(timezone.utc)
        await self.uow.audit_logs.save(session, AuditLog(
            id=str(uuid4()), actor_user_id=None, action="daily_aml_scan", resource_type="aml_batch_run", resource_id=run.id,
            detail=json.dumps({"batch_key": key, "status": run.status, "total_customers": run.total_customers, "succeeded_customers": run.succeeded_customers, "failed_customers": run.failed_customers, "created_alerts": run.created_alerts}, ensure_ascii=False),
        ))
        await session.flush()
        return run, True
