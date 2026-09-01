from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import Database
from app.models.notification import Notification


class NotificationDeliveryService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def deliver_customer_service(self, payload: dict[str, str]) -> bool:
        user_id = str(payload.get("user_id") or "")
        idempotency_key = str(payload.get("idempotency_key") or "")
        resource_id = str(payload.get("resource_id") or "")
        if not user_id or not idempotency_key or not resource_id:
            raise ValueError("notification payload missing user_id, resource_id, or idempotency_key")
        async with self.database.session_factory() as session:
            existing = (await session.execute(select(Notification.id).where(Notification.user_id == user_id, Notification.idempotency_key == idempotency_key))).scalar_one_or_none()
            if existing:
                return False
            row = Notification(
                id=str(uuid4()),
                user_id=user_id,
                notification_type=str(payload.get("notification_type") or "customer_service"),
                title=str(payload.get("title") or "客服通知"),
                content=str(payload.get("content") or ""),
                resource_type=str(payload.get("resource_type") or "customer_service_session"),
                resource_id=resource_id,
                idempotency_key=idempotency_key,
            )
            session.add(row)
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False
