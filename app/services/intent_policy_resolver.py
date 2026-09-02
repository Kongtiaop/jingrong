from __future__ import annotations

from app.schemas.intent import StructuredEntity, StructuredIntent


MANDATORY_CONSTRAINTS = {
    "must_not_submit_without_verified_customer_confirmation",
    "must_not_auto_select_product",
}


class IntentPolicyResolver:
    """Combines a deterministic baseline with optional structured LLM understanding.

    It returns a safe interpretation, never an execution authorization.
    """

    def resolve(self, *, rule_intent: StructuredIntent, llm_intent: StructuredIntent | None = None) -> StructuredIntent:
        if llm_intent is None:
            return rule_intent
        # Prefer deterministic high-confidence transaction, handoff, and read-only facts.
        if rule_intent.confidence >= 0.9 and rule_intent.template != "T0_UNKNOWN":
            return self._merge_constraints(rule_intent, llm_intent)
        # Do not let an ambiguous deterministic interpretation become a state-changing workflow
        # solely because the model inferred a product/transaction from missing conversational context.
        # 放宽范围（非交易兜底）：unknown 规则仍可被高置信 LLM 提升为 只读/推荐/转人工 等
        # 非状态变更模板（T1/T2/T5/T6/T7）—— 这类不执行任何业务动作，能减少"每个推荐问句都要加
        # 正则规则"的维护负担。交易类（T3/T4/T9/T10）依旧严禁由模型从 unknown 升级而来。
        if rule_intent.template == "T0_UNKNOWN" and llm_intent.template in {"T3_RECOMMEND_THEN_PURCHASE_DRAFT", "T4_PURCHASE_DRAFT", "T9_MODIFY_DRAFT", "T10_CANCEL_DRAFT"}:
            return rule_intent.model_copy(update={
                "constraints": sorted(set(rule_intent.constraints) | {"must_not_submit_without_verified_customer_confirmation", "must_not_auto_select_product"}),
                "requires_clarification": True,
                "explicit_confirmation": False,
            })
        merged = llm_intent.model_copy(deep=True)
        merged.constraints = sorted(set(merged.constraints) | set(rule_intent.constraints) | MANDATORY_CONSTRAINTS.intersection(set(rule_intent.constraints)))
        merged.explicit_confirmation = bool(rule_intent.explicit_confirmation and llm_intent.explicit_confirmation)
        # Never allow an LLM to remove required fields found by a deterministic rule.
        merged.missing_required_fields = sorted(set(merged.missing_required_fields) | set(rule_intent.missing_required_fields))
        for key, entity in rule_intent.entities.items():
            if entity.certainty == "explicit":
                merged.entities[key] = entity
        if merged.template in {"T3_RECOMMEND_THEN_PURCHASE_DRAFT", "T4_PURCHASE_DRAFT"}:
            merged.constraints = sorted(set(merged.constraints) | {"must_not_submit_without_verified_customer_confirmation"})
            merged.explicit_confirmation = False
        return merged

    @staticmethod
    def _merge_constraints(rule_intent: StructuredIntent, llm_intent: StructuredIntent) -> StructuredIntent:
        merged = rule_intent.model_copy(deep=True)
        merged.constraints = sorted(set(rule_intent.constraints) | set(llm_intent.constraints))
        return merged
