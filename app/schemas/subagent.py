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
    # 是否需要转人工：由"知识类子任务 low_confidence"等降级信号驱动。
    # **结构化表达而非写进正文**——正文混排会让正确内容看起来也失败了，
    # 而调用方需要的是"要不要转人工"这个判定。
    requires_handoff: bool = False
    # 本次 fan-out 的决策元数据（由 run_read_only_fanout 回填）：
    # 让响应/审计能直接回答"这次到底并行了没有、为什么"，无需反查审计表。
    fanout_mode: str = ""     # parallel / serial
    fanout_reason: str = ""   # 模型自述理由
    fanout_source: str = ""   # model / fallback_serial / disabled
    # 融合期的去重统计。**必须可见**：否则"重复被去掉多少"无从追踪，
    # 而"重复"正是本层最容易退化的地方（同一份数据/知识被子代理重复读）。
    dedup: dict = Field(default_factory=dict)


class FanoutDecision(BaseModel):
    """模型对「这一个问题要不要拆、能不能并行」的自主决策（只读查询专用）。

    为什么单独建模而不是直接给 executor：executor 只认 `SubagentTask`，
    而这里要保留**决策依据**（mode/reason/depends_on），否则事后无法回答
    "模型为什么没并行"——此前多次踩坑证明：**丢失决策留痕 = 只能靠猜**。
    """

    mode: str = "parallel"                  # parallel / serial
    tasks: list[SubagentTask] = Field(default_factory=list)
    reason: str = ""                        # 模型自述理由（审计用，不参与控制流）
    # 每个子任务（按下标）依赖的前序子任务下标；非空即代表存在依赖，必须串行
    depends_on: list[list[int]] = Field(default_factory=list)
    source: str = "model"                   # model / fallback_serial / disabled

    @property
    def has_dependency(self) -> bool:
        return any(deps for deps in self.depends_on)

    @property
    def effective_mode(self) -> str:
        """实际执行模式：**有依赖一律串行**，不采信模型自报的 mode。

        为什么（硬约束而非提示词）：提示词要求模型"有依赖就填 serial"，
        但实测模型在"先查风险限制再解释"用例上**正确填了 depends_on 却把 mode 写成 parallel**。
        若只信 mode，就会把有依赖的子任务并发跑，后者拿不到前者的事实 → 结果错误。
        故以 `depends_on` 为准（结构性证据 > 模型自述）。
        """
        if self.has_dependency:
            return "serial"
        return "parallel" if self.mode == "parallel" else "serial"
