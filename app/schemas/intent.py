from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


IntentTemplateCode = Literal[
    "T1_INFORMATION_QUERY",
    "T2_PRODUCT_RECOMMENDATION",
    "T3_RECOMMEND_THEN_PURCHASE_DRAFT",
    "T4_PURCHASE_DRAFT",
    "T5_ORDER_STATUS",
    "T6_RISK_EXPLANATION",
    "T7_HUMAN_HANDOFF",
    "T8_MULTI_INTENT",
    "T9_MODIFY_DRAFT",
    "T10_CANCEL_DRAFT",
    "T0_UNKNOWN",
]


class StructuredEntity(BaseModel):
    value: Any | None = None
    certainty: Literal["explicit", "approximate", "inferred", "missing"] = "missing"


class StructuredIntent(BaseModel):
    """Constrained model/fallback output. It is never an execution command."""

    template: IntentTemplateCode
    primary_intent: str
    secondary_intents: list[str] = Field(default_factory=list)
    entities: dict[str, StructuredEntity] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    requires_clarification: bool = False
    explicit_confirmation: bool = False
    confidence: float = Field(ge=0, le=1)
    source: Literal["rule", "llm_fallback"] = "rule"


class IntentTemplate(BaseModel):
    code: IntentTemplateCode
    description: str
    execution_mode: Literal["read_only", "plan_rules", "controlled_workflow"]
    required_fields: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
