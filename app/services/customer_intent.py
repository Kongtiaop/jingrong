from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

IntentName = Literal[
    "greeting", "human_handoff", "workorder_status", "order_status", "risk_restriction",
    "asset_summary", "product_status", "company_info", "company_business", "faq",
    "workflow", "policy", "risk_public", "product_recommendation", "business_operation", "unknown"
]

@dataclass(frozen=True)
class IntentTask:
    task_id: str
    intent: str
    route: str
    query: str
    priority: str = "normal"
    depends_on: list[str] = field(default_factory=list)
    required: bool = True
    arguments: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    status: str = "planned"
    execution_mode: str = "controlled_workflow"

@dataclass(frozen=True)
class IntentPlan:
    normalized_query: str
    tasks: list[IntentTask]
    clarification: str | None = None
    requires_handoff: bool = False

REACT_INTENTS = {"company_info", "company_business", "faq", "workflow", "policy", "risk_public"}


def execution_mode_for_intent(intent: str) -> str:
    return "bounded_react" if intent in REACT_INTENTS else "controlled_workflow"


def extract_entities(query: str) -> dict[str, str]:
    """Extract explicit identifiers without guessing business meaning."""
    text = query.strip()
    entities: dict[str, str] = {}
    patterns = {
        "order_no": r"\b(?:ORD[-_A-Z0-9]+|订单号[:：]?\s*[A-Z0-9_-]{6,})\b",
        "ticket_no": r"\b(?:WO[-_A-Z0-9]+|工单号[:：]?\s*[A-Z0-9_-]{6,})\b",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(0).strip()
            value = re.sub(r"^(订单号|工单号)[:：]?\s*", "", value, flags=re.IGNORECASE)
            entities[key] = value
    product_match = re.search(r'(?:产品名称|产品)[:：]?\s*[“\"]?(.+?)(?=现在|目前|是否|状态|在售|停售|详情|$)', text)
    if product_match:
        candidate = product_match.group(1).strip()
        candidate = re.split(r'(?:现在|目前|是否|状态|在售|停售|详情)', candidate, maxsplit=1)[0].strip()
        if candidate:
            entities["product_name"] = candidate
    return entities


class CustomerIntentPlanner:
    _patterns: list[tuple[str, str, str, float]] = [
        ("human_handoff", "人工", "handoff", .99), ("human_handoff", "客服接入", "handoff", .99),
        ("workorder_status", "工单", "business_fact", .95), ("order_status", "订单", "business_fact", .90),
        ("risk_restriction", "风险限制", "business_fact", .96), ("risk_restriction", "账户限制", "business_fact", .96),
        ("risk_restriction", "冻结", "business_fact", .92), ("asset_summary", "资产", "business_fact", .92),
        ("asset_summary", "持仓", "business_fact", .92), ("asset_summary", "余额", "business_fact", .90),
        ("product_status", "产品状态", "business_fact", .95), ("product_status", "是否在售", "business_fact", .94),
        ("product_recommendation", "推荐产品", "suitability", .96), ("product_recommendation", "适合我的产品", "suitability", .96), ("product_recommendation", "推荐", "suitability", .88),
        ("business_operation", "申购", "draft_operation", .98), ("business_operation", "购买", "draft_operation", .98), ("business_operation", "买入", "draft_operation", .98), ("business_operation", "赎回", "draft_operation", .98), ("business_operation", "转账", "draft_operation", .98), ("business_operation", "下单", "draft_operation", .98),
        ("company_info", "公司", "rag_explanation", .70), ("company_business", "业务", "rag_explanation", .75),
        ("faq", "怎么", "rag_explanation", .55), ("workflow", "流程", "rag_explanation", .80),
        ("policy", "政策", "rag_explanation", .80), ("risk_public", "风险", "rag_explanation", .70),
    ]

    def plan(self, query: str) -> IntentPlan:
        normalized = re.sub(r"\s+", "", query.strip().lower())
        if not normalized:
            return IntentPlan(normalized_query="", tasks=[], clarification="请描述您需要查询或办理的事项。")
        if re.match(r"^(你好|您好|嗨|hello|hi|谢谢|感谢|再见)[!！。？? ]*$", normalized):
            return IntentPlan(normalized_query=normalized, tasks=[IntentTask("t1", "greeting", "direct", query, confidence=.99, arguments=extract_entities(query), execution_mode=execution_mode_for_intent("greeting"))])
        if any(x in normalized for x in ("人工", "客服接入", "转人工")):
            return IntentPlan(normalized_query=normalized, tasks=[IntentTask("t1", "human_handoff", "handoff", query, priority="high", confidence=.99, arguments=extract_entities(query), execution_mode=execution_mode_for_intent("human_handoff"))], requires_handoff=True)
        tasks=[]; seen=set()
        for intent, marker, route, confidence in self._patterns:
            if marker in normalized and intent not in seen:
                task_id=f"t{len(tasks)+1}"; depends=[]
                if intent in {"workflow", "policy", "faq"} and any(t.intent in {"order_status","workorder_status"} for t in tasks):
                    depends=[next(t.task_id for t in tasks if t.intent in {"order_status","workorder_status"})]
                if intent == "business_operation" and any(t.intent == "product_recommendation" for t in tasks):
                    depends=[next(t.task_id for t in tasks if t.intent == "product_recommendation")]
                tasks.append(IntentTask(task_id, intent, route, query, confidence=confidence, depends_on=depends, arguments=extract_entities(query), execution_mode=execution_mode_for_intent(intent))); seen.add(intent)
        if not tasks:
            return IntentPlan(normalized_query=normalized, tasks=[IntentTask("t1", "unknown", "handoff", query, confidence=.20, execution_mode=execution_mode_for_intent("unknown"))], clarification="您想查询哪一类信息，例如订单、工单、资产、风险限制或公司业务？")
        return IntentPlan(normalized_query=normalized, tasks=tasks, requires_handoff=any(t.intent == "human_handoff" for t in tasks))

