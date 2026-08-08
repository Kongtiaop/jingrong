from datetime import datetime

from pydantic import BaseModel


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    notification_type: str
    title: str
    content: str
    resource_type: str
    resource_id: str
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int


class NotificationMarkReadResponse(BaseModel):
    updated: int
    unread_count: int
