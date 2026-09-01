from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.models.workorder import WorkOrder


async def create_workorder_notification(
    session: AsyncSession,
    *,
    user_id: str | None,
    notification_type: str,
    title: str,
    content: str,
    work_order: WorkOrder,
) -> None:
    if not user_id:
        return
    session.add(
        Notification(
            id=str(uuid4()),
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            content=content,
            resource_type="work_order",
            resource_id=work_order.id,
        )
    )


async def enqueue_customer_service_notification(
    session: AsyncSession,
    *,
    user_id: str | None,
    notification_type: str,
    title: str,
    content: str,
    session_id: str,
    event_key: str,
) -> None:
    if not user_id:
        return
    from app.models.outbox import OutboxEvent

    session.add(OutboxEvent(
        id=str(uuid4()),
        event_type="notification.customer_service",
        aggregate_type="customer_service_session",
        aggregate_id=session_id,
        payload_json={
            "user_id": user_id,
            "notification_type": notification_type,
            "title": title,
            "content": content,
            "resource_type": "customer_service_session",
            "resource_id": session_id,
            "idempotency_key": event_key,
        },
        status="pending",
    ))


async def create_customer_service_notification(
    session: AsyncSession,
    *,
    user_id: str | None,
    notification_type: str,
    title: str,
    content: str,
    session_id: str,
    idempotency_key: str | None = None,
) -> None:
    if not user_id:
        return
    session.add(Notification(
        id=str(uuid4()),
        user_id=user_id,
        notification_type=notification_type,
        title=title,
        content=content,
        resource_type="customer_service_session",
        resource_id=session_id,
        idempotency_key=idempotency_key,
    ))
