from pydantic import BaseModel, Field


class AgentState(BaseModel):
    request_id: str
    trace_id: str
    user_id: str | None = None
    customer_id: str | None = None
    role: str | None = None
    user_message: str
    messages: list[dict] = Field(default_factory=list)
    selected_agents: list[str] = Field(default_factory=list)
    agent_results: list[dict] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    confirmed_memory_candidates: list[dict] = Field(default_factory=list)
    pending_confirmation: dict | None = None
    iteration_count: int = 0
    tool_call_count: int = 0
    token_budget: int = 0
    error: str | None = None
    final_response: str | None = None
    # LangGraph 编排字段：真实节点间传递的中间状态
    plan: dict = Field(default_factory=dict)          # CustomerIntentPlanner 产物（可序列化）
    route: str = ""                                    # route_for_plan 结果
    task_results: list[dict] = Field(default_factory=list)  # CustomerTaskExecutor 产物
    clarification_count: int = 0                       # 澄清计数（>=2 转人工）
    overall_status: str = ""                           # needs_clarification / handoff / succeeded / ...
    needs_handoff: bool = False
    # 只读 ReAct 循环状态（read-only 知识意图走 LangGraph 条件循环）
    react_query: str = ""                              # 当前待处理查询（含改写）
    react_attempts: list[dict] = Field(default_factory=list)  # 每轮工具调用记录
    seen_signatures: list[str] = Field(default_factory=list)  # 结果哈希集合（早停）
    react_result: dict | None = None                   # 循环结束时的任务结果


class AgentResult(BaseModel):
    agent_name: str
    status: str
    summary: str
    data: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_confirmation: bool = False
    next_action: str | None = None
    errors: list[str] = Field(default_factory=list)
