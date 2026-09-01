from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select

from app.models.auth import CustomerAdvisorAuthorization
from app.models.outbox import OutboxEvent
from app.services.permission_scoped_cache import bump_subject_scope_version


@dataclass(frozen=True)
class AuthorizationMutation:
    authorization: CustomerAdvisorAuthorization
    created: bool


class AdvisorAuthorizationService:
    """Single write boundary for customer-advisor authorization mutations."""

    @staticmethod
    async def grant_or_update(session, *, customer_id: str, advisor_id: str, values: dict[str, Any]) -> AuthorizationMutation:
        authorization = (await session.execute(
            select(CustomerAdvisorAuthorization).where(
                CustomerAdvisorAuthorization.customer_id == customer_id,
                CustomerAdvisorAuthorization.advisor_id == advisor_id,
                CustomerAdvisorAuthorization.status == "active",
            ).with_for_update()
        )).scalars().first()
        if authorization is None:
            authorization = CustomerAdvisorAuthorization(
                id=str(uuid4()), customer_id=customer_id, advisor_id=advisor_id, **values
            )
            session.add(authorization)
            return AuthorizationMutation(authorization=authorization, created=True)
        for key, value in values.items():
            setattr(authorization, key, value)
        authorization.status = "active"
        authorization.revoked_at = None
        authorization.effective_to = None
        return AuthorizationMutation(authorization=authorization, created=False)

    @staticmethod
    async def revoke_ids(session, *, authorization_ids: Iterable[str]) -> list[CustomerAdvisorAuthorization]:
        ids = {str(item) for item in authorization_ids if item}
        if not ids:
            return []
        rows = list((await session.execute(
            select(CustomerAdvisorAuthorization).where(
                CustomerAdvisorAuthorization.id.in_(ids),
                CustomerAdvisorAuthorization.status == "active",
            ).with_for_update()
        )).scalars().all())
        if {row.id for row in rows} != ids:
            raise LookupError("one or more active authorizations were not found")
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = "revoked"
            row.revoked_at = now
        return rows

    @staticmethod
    async def revoke_for_customer(session, *, authorization_id: str, customer_id: str) -> CustomerAdvisorAuthorization | None:
        row = (await session.execute(
            select(CustomerAdvisorAuthorization).where(
                CustomerAdvisorAuthorization.id == authorization_id,
                CustomerAdvisorAuthorization.customer_id == customer_id,
                CustomerAdvisorAuthorization.status == "active",
            ).with_for_update()
        )).scalar_one_or_none()
        if row is None:
            return None
        row.status = "revoked"
        row.revoked_at = datetime.now(timezone.utc)
        return row

    @staticmethod
    def enqueue_scope_invalidation_events(session, *, advisor_ids: Iterable[str], mutation_source: str) -> list[str]:
        """Persist invalidations in the caller transaction so retries survive process failure."""
        event_ids: list[str] = []
        for advisor_id in sorted({str(item) for item in advisor_ids if item}):
            event_id = str(uuid4())
            session.add(OutboxEvent(
                id=event_id,
                event_type="authorization.subject_scope_invalidate",
                aggregate_type="advisor_authorization",
                aggregate_id=None,
                payload_json={"advisor_id": advisor_id, "mutation_source": mutation_source},
                status="pending",
            ))
            event_ids.append(event_id)
        return event_ids

    @staticmethod
    async def invalidate_subject_scopes(redis, advisor_ids: Iterable[str]) -> None:
        """Compatibility helper for explicit operational repair scripts."""
        if redis is None:
            return
        for advisor_id in sorted({str(item) for item in advisor_ids if item}):
            await bump_subject_scope_version(redis, user_id=advisor_id)
