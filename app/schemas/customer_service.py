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


class UnifiedAgentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    conversation_id: str | None = Field(default=None, max_length=128)
    page_context: str = Field(default="", max_length=256)
    resource_id: str | None = Field(default=None, max_length=128)
    # staff/advisor 把只读分析聚焦到某个目标客户时传入；customer 一律不允许带（防越权读他人）
    target_customer_id: str | None = Field(default=None, max_length=128)


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
    execution_mode: str = "controlled_workflow"


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
    route: str = "customer_service"
    agents: list[str] = Field(default_factory=lambda: ["CustomerServiceAgent"])
    available_agents: list[str] = Field(default_factory=list)
    denied_agents: list[str] = Field(default_factory=list)
    page_context: str = ""
    results: list[TaskExecutionResultResponse]
    clarification: str | None = None
    requires_handoff: bool = False
    # 转人工改为「建议 + 确认」后新增：
    #   handoff_suggested = 系统判定该转人工，但**尚未建单**，等用户确认
    #   handoff_reason    = 建议理由（low_confidence / clarification_exhausted）
    # 为什么不让 AI 直接转：转人工是有副作用的动作（建工单、占用人工坐席、通知客服），
    # 按项目一贯原则应交由确定性流程 + 用户确认，与交易草稿的「预检→确认」一致。
    # 注意：用户**主动**要求转人工时仍直接建单（那是明确意图），不走确认。
    handoff_suggested: bool = False
    handoff_reason: str | None = None
    work_order_id: str | None = None
    ticket_no: str | None = None
    customer_service_session_id: str | None = None
    customer_service_session_status: str | None = None


class AnalysisChartSpec(BaseModel):
    type: str = Field(pattern="^(metric|table|bar|line|pie)$")
    title: str
    dimension: str | None = None
    metric: str | None = None
    unit: str = ""


class AnalysisQueryPlanResponse(BaseModel):
    mode: str
    metric_code: str | None = None
    source_tables: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    required_scope: str = "internal_authorized_scope"
    read_only: bool = True
    free_sql: bool = False
    next_guard: str = "catalog_template_parameter_binding"


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
