from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.auth import CustomerAdvisorAuthorization


class SubjectScopeService:
    """Deterministic subject scope for read-only customer data access."""

    @staticmethod
    async def authorized_subject_ids(session, user) -> set[str]:
        if user.is_super_admin:
            return set()  # Empty means unrestricted only for super_admin callers.
        role_codes = {role.code for role in user.roles}
        if role_codes & {"advisor", "customer_manager"}:
            now = datetime.now(timezone.utc)
            rows = await session.execute(
                select(CustomerAdvisorAuthorization.customer_id).where(
                    CustomerAdvisorAuthorization.advisor_id == user.id,
                    CustomerAdvisorAuthorization.status == "active",
                    CustomerAdvisorAuthorization.effective_from <= now,
                    (CustomerAdvisorAuthorization.effective_to.is_(None) | (CustomerAdvisorAuthorization.effective_to >= now)),
                    CustomerAdvisorAuthorization.revoked_at.is_(None),
                )
            )
            return {str(value) for value in rows.scalars().all()}
        # Staff without an explicit assignment scope may only query their own subject context.
        return {str(user.id)}
