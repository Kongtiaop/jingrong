from datetime import datetime
from pydantic import BaseModel


class WorkOrderAttachmentResponse(BaseModel):
    id: str
    work_order_id: str
    update_id: str | None
    uploaded_by: str
    original_name: str
    content_type: str
    size_bytes: int
    created_at: datetime
