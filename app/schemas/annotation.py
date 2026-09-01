from datetime import datetime
from pydantic import BaseModel, Field


class BadCaseResponse(BaseModel):
    id: str
    query: str
    intent: str = ""
    confidence: float | None = None
    evidence_status: str = ""
    handoff: bool = False
    clarifications: int = 0
    reasons: list[str] = Field(default_factory=list)
    status: str = "pending"
    created_at: datetime
    # 人工标注字段
    labeled_intent: str = ""
    requires_clarification: bool = False
    category: str = ""
    note: str = ""
    split: str = "train"
    labeled_by: str | None = None
    labeled_at: datetime | None = None


class BadCaseListResponse(BaseModel):
    items: list[BadCaseResponse]
    total: int


class LabelBadCaseRequest(BaseModel):
    intent: str = ""                      # 正确意图码；空表示仅跳过/不纳入黄金集
    requires_clarification: bool = False  # 该 query 是否需澄清
    category: str = ""                    # 语义类别（可选）
    note: str = ""                        # 标注备注
    split: str = "train"                  # train/val/test
    discard: bool = False                 # True=标注为"非有效 bad case"，不导出进黄金集


class LabelBadCaseResponse(BaseModel):
    id: str
    status: str
    labeled_intent: str
    split: str


class AnnotationSummaryResponse(BaseModel):
    pending: int
    reviewed: int
    labeled: int
    discarded: int
