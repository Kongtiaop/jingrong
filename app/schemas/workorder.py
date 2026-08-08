from datetime import datetime
from pydantic import BaseModel, Field


class WorkOrderCreateRequest(BaseModel):
    customer_id: str | None = None
    category: str = Field(pattern=r"^(customer_service|complaint|risk_investigation|business_exception)$")
    priority: str = Field(default="normal", pattern=r"^(low|normal|high|urgent)$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)


class WorkOrderAssignRequest(BaseModel):
    assigned_to: str


class WorkOrderHandleRequest(BaseModel):
    status: str = Field(pattern=r"^(in_progress|pending_customer|resolved)$")
    note: str = Field(default="", max_length=10000)


class WorkOrderResponse(BaseModel):
    id: str
    ticket_no: str
    customer_id: str | None
    created_by: str
    assigned_to: str | None
    category: str
    priority: str
    status: str
    title: str
    description: str
    resolution_note: str
    risk_alert_id: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None


class WorkOrderListResponse(BaseModel):
    items: list[WorkOrderResponse]
    total: int

class WorkOrderCustomerSupplementRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class WorkOrderUpdateResponse(BaseModel):
    id: str
    work_order_id: str
    author_user_id: str
    author_role: str
    content: str
    created_at: datetime

class WorkOrderTodoSummaryResponse(BaseModel):
    total: int
    open: int
    assigned: int
    in_progress: int
    pending_customer: int
    resolved: int
    urgent: int
    high: int
