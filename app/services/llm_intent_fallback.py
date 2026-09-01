from __future__ import annotations

import json
from typing import Protocol

from app.schemas.intent import StructuredIntent


class StructuredIntentProvider(Protocol):
    async def classify(self, *, query: str, conversation_summary: str = "") -> dict: ...


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
        result = StructuredIntent.model_validate(payload)
        if result.source != "llm_fallback":
            result = result.model_copy(update={"source": "llm_fallback"})
        # Defense in depth: a model cannot claim an implicit customer confirmation.
        if result.template in {"T3_RECOMMEND_THEN_PURCHASE_DRAFT", "T4_PURCHASE_DRAFT"}:
            result = result.model_copy(update={"explicit_confirmation": False})
            result.constraints = sorted(set(result.constraints) | {"must_not_submit_without_verified_customer_confirmation"})
        return result
