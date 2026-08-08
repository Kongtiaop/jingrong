from datetime import datetime
from pydantic import BaseModel, Field


class CustomerServiceSessionResponse(BaseModel):
    id: str
    customer_id: str
    work_order_id: str | None
    status: str
    mode: str
    assigned_agent_id: str | None
    handoff_reason: str
    handoff_at: datetime
    accepted_at: datetime | None
    ended_at: datetime | None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime
    agent_last_heartbeat_at: datetime | None = None


class CustomerServiceActionRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class CustomerServiceAssignRequest(BaseModel):
    assigned_agent_id: str
    note: str = Field(default="", max_length=2000)


class IntentTaskResponse(BaseModel):
    task_id: str
    intent: str
    route: str
    query: str
    priority: str
    depends_on: list[str]
    required: bool
    arguments: dict
    confidence: float
    status: str


class CustomerIntentPlanResponse(BaseModel):
    normalized_query: str
    tasks: list[IntentTaskResponse]
    clarification: str | None = None
    requires_handoff: bool = False
    work_order_id: str | None = None
    ticket_no: str | None = None
    customer_service_session_id: str | None = None
    customer_service_session_status: str | None = None


class TaskExecutionResultResponse(BaseModel):
    task_id: str
    intent: str
    status: str
    answer: str = ""
    data: dict = Field(default_factory=dict)
    error: str | None = None
    clarification: str | None = None


class CustomerTaskExecutionResponse(BaseModel):
    conversation_id: str
    overall_status: str
    answer: str
    results: list[TaskExecutionResultResponse]
    clarification: str | None = None
    requires_handoff: bool = False
    work_order_id: str | None = None
    ticket_no: str | None = None
    customer_service_session_id: str | None = None
    customer_service_session_status: str | None = None


class CustomerServiceMessageResponse(BaseModel):
    id: str
    session_id: str
    sender_type: str
    sender_user_id: str | None
    content: str
    message_type: str
    source: str
    created_at: datetime


class CustomerServiceMessageCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
