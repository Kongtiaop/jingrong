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
