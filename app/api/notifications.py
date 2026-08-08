from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, update

from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.models.auth import User
from app.models.notification import Notification
from app.schemas.notification import NotificationListResponse, NotificationMarkReadResponse, NotificationResponse

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _response(row: Notification) -> NotificationResponse:
    return NotificationResponse.model_validate(row, from_attributes=True)


async def _unread_count(session, user_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Notification).where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
            )
        ).scalar_one()
    )


@router.get("", response_model=ApiResponse[NotificationListResponse])
async def list_notifications(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: Annotated[User, Depends(current_user)] = None,
):
    async with request.app.state.database.session_factory() as session:
        query = select(Notification).where(Notification.user_id == user.id)
        count_query = select(func.count()).select_from(Notification).where(Notification.user_id == user.id)
        if unread_only:
            query = query.where(Notification.is_read.is_(False))
            count_query = count_query.where(Notification.is_read.is_(False))
        total = int((await session.execute(count_query)).scalar_one())
        rows = list(
            (
                await session.execute(
                    query.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
                )
            ).scalars().all()
        )
        return ApiResponse(
            data=NotificationListResponse(
                items=[_response(row) for row in rows],
                total=total,
                unread_count=await _unread_count(session, user.id),
            )
        )


@router.post("/{notification_id}/read", response_model=ApiResponse[NotificationMarkReadResponse])
async def mark_notification_read(
    request: Request,
    notification_id: str,
    user: Annotated[User, Depends(current_user)],
):
    async with request.app.state.database.session_factory() as session:
        row = await session.get(Notification, notification_id, with_for_update=True)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "NOTIFICATION_NOT_FOUND", "message": "notification not found"})
        if row.user_id != user.id:
            raise HTTPException(status_code=403, detail={"code": "NOTIFICATION_SCOPE", "message": "notification is outside current user scope"})
        updated = 0
        if not row.is_read:
            row.is_read = True
            row.read_at = datetime.now(timezone.utc)
            updated = 1
            await session.commit()
        return ApiResponse(data=NotificationMarkReadResponse(updated=updated, unread_count=await _unread_count(session, user.id)))


@router.post("/read-all", response_model=ApiResponse[NotificationMarkReadResponse])
async def mark_all_notifications_read(
    request: Request,
    user: Annotated[User, Depends(current_user)],
):
    async with request.app.state.database.session_factory() as session:
        result = await session.execute(
            update(Notification)
            .where(Notification.user_id == user.id, Notification.is_read.is_(False))
            .values(is_read=True, read_at=datetime.now(timezone.utc))
        )
        await session.commit()
        return ApiResponse(data=NotificationMarkReadResponse(updated=int(result.rowcount or 0), unread_count=0))
