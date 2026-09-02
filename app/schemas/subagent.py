# -*- coding: utf-8 -*-
"""受控只读子代理并行层的协议定义（受控子代理并行层设计方案 P0）。

SubagentTask  = 一个可并行的只读领域子任务（capability 白名单内）。
SubagentResult = 该子任务的**结构化**结果（不是自由文本）。
SubagentMergeResult = Supervisor 对多个子结果的确定性融合结果。

只读能力白名单见 services/subagent_executor.py；写/资金/订单/风控结论绝不进此层。
"""
from pydantic import BaseModel, Field


class SubagentTask(BaseModel):
    task_id: str
    query: str
    read_only_capability: str  # 标识走哪个只读子代理/处理器
    tool_whitelist: list[str] = Field(default_factory=list)
    scope_json: dict = Field(default_factory=dict)      # authorized_subject_ids 等数据范围
    budget_json: dict = Field(default_factory=dict)      # token/迭代预算元数据


class SubagentResult(BaseModel):
    task_id: str
    status: str = "failed"  # success / failed / partial
    summary: str = ""
    data: dict = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    error: str = ""


class SubagentMergeResult(BaseModel):
    results: list[SubagentResult] = Field(default_factory=list)
    overall_status: str = ""  # succeeded / partial / failed
    answer: str = ""
