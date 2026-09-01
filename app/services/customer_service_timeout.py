from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.customer_service import CustomerServiceSession


class CustomerServiceTimeoutService:
    """Pause stale active sessions and expire abandoned waiting sessions."""

    async def sweep(self, session, *, timeout_seconds: int, waiting_timeout_seconds: int = 1800, cleanup_waiting: bool = True, actor_user_id: str | None = None, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        cutoff = now.timestamp() - timeout_seconds
        rows = list((await session.execute(
            select(CustomerServiceSession)
            .where(
                CustomerServiceSession.status == "human_active",
                CustomerServiceSession.agent_last_heartbeat_at.is_not(None),
            )
            .with_for_update()
        )).scalars().all())
        changed: list[str] = []
        expired: list[str] = []
        for row in rows:
            heartbeat = row.agent_last_heartbeat_at.timestamp() if row.agent_last_heartbeat_at else 0
            if heartbeat < cutoff:
                row.status = "paused"
                row.assigned_agent_id = None
                row.agent_last_heartbeat_at = None
                session.add(AuditLog(
                    id=str(uuid4()),
                    actor_user_id=actor_user_id,
                    action="customer_service_session_timeout_pause",
                    resource_type="customer_service_session",
                    resource_id=row.id,
                    detail=f"timeout_seconds={timeout_seconds};source=worker",
                ))
                changed.append(row.id)
        if cleanup_waiting:
            waiting_cutoff = now.timestamp() - waiting_timeout_seconds
            waiting_rows = list((await session.execute(
                select(CustomerServiceSession)
                .where(
                    CustomerServiceSession.status == "waiting_human",
                    CustomerServiceSession.created_at < datetime.fromtimestamp(waiting_cutoff, tz=timezone.utc),
                )
                .with_for_update()
            )).scalars().all())
            for row in waiting_rows:
                row.status = "expired"
                row.ended_at = now
                session.add(AuditLog(
                    id=str(uuid4()),
                    actor_user_id=actor_user_id,
                    action="customer_service_session_waiting_timeout_expire",
                    resource_type="customer_service_session",
                    resource_id=row.id,
                    detail=f"waiting_timeout_seconds={waiting_timeout_seconds};source=worker",
                ))
                expired.append(row.id)
        await session.commit()
        return {"paused_count": len(changed), "session_ids": changed, "expired_count": len(expired), "expired_session_ids": expired, "timeout_seconds": timeout_seconds, "waiting_timeout_seconds": waiting_timeout_seconds}
