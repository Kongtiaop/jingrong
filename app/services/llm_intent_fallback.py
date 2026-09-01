from __future__ import annotations

import json
import os
from typing import Protocol

from app.schemas.intent import StructuredIntent, StructuredIntentTask
from app.services.intent_normalizer import IntentNormalizer, canonical_template

# 线上 bad case 回流开关：生产设 BAD_CASE_COLLECT=1 时，意图 unknown/低置信会落积累池。
_COLLECT_BAD_CASE = os.environ.get("BAD_CASE_COLLECT") == "1"


class StructuredIntentProvider(Protocol):
    async def classify(self, *, query: str, conversation_summary: str = "") -> dict: ...


def _rules_fallback(query: str) -> StructuredIntent:
    """LLM 解析失败时的规则规划器兜底（比 unknown 更准；对抗/改数由非法变更门禁兜住）。"""
    from app.services.customer_intent import CustomerIntentPlanner

    plan = CustomerIntentPlanner().plan(query or "")
    intents = [t.intent for t in plan.tasks]
    if not intents:
        intents = ["unknown"]
    clarify = any(i == "unknown" for i in intents) or bool(plan.clarification)
    primary = intents[0]
    tasks = []
    for intent in intents:
        tpl = canonical_template(intent, None)
        tasks.append(StructuredIntentTask(
            intent=intent,
            task_type="trade_workflow" if tpl in {"T4_PURCHASE_DRAFT", "T3_RECOMMEND_THEN_PURCHASE_DRAFT"} else "read_only_query",
            template=tpl,
            requires_clarification=clarify,
            confidence=0.7,
        ))
    template = "T8_MULTI_INTENT" if len(tasks) > 1 else (tasks[0].template if tasks else "T0_UNKNOWN")
    return StructuredIntent(
        template=template,
        primary_intent=primary,
        secondary_intents=sorted(set(intents[1:])),
        requires_clarification=clarify,
        confidence=0.7,
        source="llm_fallback",
        tasks=tasks,
    )


def _apply_safety_gates(result: StructuredIntent, query: str) -> StructuredIntent:
    """LLM 与规则回退共用：非法改数据硬门禁 + 交易确认约束（defense in depth）。"""
    if _is_illegal_mutation(query):
        return StructuredIntent(
            template="T0_UNKNOWN", primary_intent="unknown", confidence=0.1,
            requires_clarification=True, source="llm_fallback",
            constraints=["must_not_modify_business_data"],
        )
    if result.template in {"T3_RECOMMEND_THEN_PURCHASE_DRAFT", "T4_PURCHASE_DRAFT"}:
        result = result.model_copy(update={"explicit_confirmation": False})
        result.constraints = sorted(set(result.constraints) | {"must_not_submit_without_verified_customer_confirmation"})
    return result


def _coerce_entities(entities: dict) -> dict:
    """模型常把实体值当字符串返回；统一包成 {value, certainty} 对象。

    模型也常把 certainty 当数字（如 0.98）或非枚举字符串返回，会击穿
    StructuredEntity 的 Literal 校验 → 这里统一归一到合法枚举，避免 schema 崩溃。
    """
    CERTAINTY_ENUM = {"explicit", "approximate", "inferred", "missing"}

    def _certainty(raw) -> str:
        if raw is None:
            return "explicit"
        if isinstance(raw, bool):
            return "explicit" if raw else "inferred"
        if isinstance(raw, (int, float)):
            if raw >= 0.7:
                return "explicit"
            if raw >= 0.4:
                return "approximate"
            return "inferred"
        c = str(raw).strip().lower()
        return c if c in CERTAINTY_ENUM else "explicit"

    out: dict = {}
    for key, value in entities.items():
        if isinstance(value, dict):
            out[key] = {"value": value.get("value"), "certainty": _certainty(value.get("certainty"))}
        else:
            out[key] = {"value": value, "certainty": "explicit" if value is not None else "missing"}
    return out


# 非法改数据（改余额/删账户/注销）→ 强制 unknown + 拦截，绝不当成业务操作执行。
# 注意：访问他人数据（"看别人余额"）由 SubjectScope 执行层拦，不在意图层强制 unknown。
_ILLEGAL_MUTATION = ["改成", "修改余额", "改为", "把余额", "提高余额", "删除", "注销"]


def _is_illegal_mutation(query: str) -> bool:
    return any(marker in query for marker in _ILLEGAL_MUTATION)


class FixtureIntentProvider:
    """Deterministic test provider used before real model rollout."""

    def __init__(self, fixtures: dict[str, dict]):
        self.fixtures = fixtures

    async def classify(self, *, query: str, conversation_summary: str = "") -> dict:
        return self.fixtures.get(query, {
            "template": "T0_UNKNOWN", "primary_intent": "unknown", "confidence": 0.1,
            "requires_clarification": True, "source": "llm_fallback",
        })


class LLMIntentFallbackService:
    """Schema-enforced fallback; caller decides when it is eligible.

    This layer never creates drafts, changes business state, or authorizes execution.
    """

    def __init__(self, provider: StructuredIntentProvider):
        self.provider = provider

    async def classify(self, *, query: str, conversation_summary: str = "") -> StructuredIntent:
        payload = await self.provider.classify(query=query, conversation_summary=conversation_summary)
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("structured intent payload must be an object")
        payload = dict(payload)
        # 模型 Function Calling 解析失败时，provider 标记走规则规划器兜底（比 unknown 更准）
        if payload.get("source") == "rules_fallback_needed":
            return _apply_safety_gates(_rules_fallback(query), query)
        if payload.get("tasks") is None:
            payload["tasks"] = []
        if not isinstance(payload.get("tasks"), list):
            raise ValueError("structured intent tasks must be a list")
        normalized_tasks = []
        for task in payload["tasks"]:
            if not isinstance(task, dict):
                raise ValueError("structured intent task must be an object")
            task = dict(task)
            task.setdefault("entities", {})
            constraints = task.get("constraints")
            if constraints is None:
                task["constraints"] = []
            elif isinstance(constraints, str):
                task["constraints"] = [constraints]
            elif isinstance(constraints, dict):
                task["constraints"] = [key for key, value in constraints.items() if value]
            elif not isinstance(constraints, list):
                raise ValueError("structured intent task constraints must be a list")
            normalized_tasks.append(task)
        payload["tasks"] = normalized_tasks
        for field in ("secondary_intents", "constraints", "missing_required_fields"):
            value = payload.get(field)
            if value is None:
                payload[field] = []
            elif isinstance(value, str):
                payload[field] = [value]
            elif isinstance(value, dict):
                payload[field] = list(value.keys())
        entities = payload.get("entities")
        if entities is None:
            payload["entities"] = {}
        elif not isinstance(entities, dict):
            raise ValueError("structured intent entities must be an object")
        else:
            payload["entities"] = _coerce_entities(entities)
        for task in payload.get("tasks", []):
            if isinstance(task, dict):
                task["entities"] = _coerce_entities(task.get("entities") or {})
        # 模板容错：模型 template 不可靠时按意图派生，避免 Literal 校验崩溃
        payload["template"] = canonical_template(payload.get("primary_intent"), payload.get("template"))
        for task in payload.get("tasks", []):
            if isinstance(task, dict):
                task["template"] = canonical_template(task.get("intent"), task.get("template"))
        result = StructuredIntent.model_validate(payload)
        if result.source != "llm_fallback":
            result = result.model_copy(update={"source": "llm_fallback"})
        result = IntentNormalizer().normalize(result)
        # 安全门禁：非法改数据硬门禁 + 交易确认约束（LLM 与规则回退共用）
        result = _apply_safety_gates(result, query)
        # 线上 bad case 回流（默认关；异常安全）：意图 unknown/低置信也落积累池
        if _COLLECT_BAD_CASE:
            try:
                from app.services.bad_case_collector import BadCaseStore
                BadCaseStore().collect(query, intent=result)
            except Exception:  # noqa: BLE001 - 采集失败不影响主流程
                pass
        return result
