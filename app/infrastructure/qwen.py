from __future__ import annotations

import httpx
from openai import AsyncOpenAI

from app.core.settings import Settings
from app.common.logging.config import get_logger


logger = get_logger(__name__)


class QwenProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = (
            AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.qwen_base_url,
                http_client=httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(120.0, connect=30.0)),
            )
            if settings.dashscope_api_key
            else None
        )

    async def check_config(self) -> dict[str, str]:
        if self._client is None:
            return {"status": "skipped", "reason": "DASHSCOPE_API_KEY is not configured"}
        return {"status": "configured", "model": self.settings.qwen_embedding_model}

    async def classify(self, *, query: str, conversation_summary: str = "") -> dict:
        """StructuredIntentProvider-compatible alias for intent fallback."""
        return await self.classify_intent(query=query, conversation_summary=conversation_summary)

    async def classify_intent(self, *, query: str, conversation_summary: str = "") -> dict:
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        system = (
            "你是财富管理客服意图解析器。只返回 JSON，不执行任何交易。"
            "JSON 字段必须为 template, primary_intent, secondary_intents, entities, constraints, "
            "missing_required_fields, requires_clarification, explicit_confirmation, confidence, source。"
            "template 只能是 T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T3_RECOMMEND_THEN_PURCHASE_DRAFT, "
            "T4_PURCHASE_DRAFT,T5_ORDER_STATUS,T6_RISK_EXPLANATION,T7_HUMAN_HANDOFF,T8_MULTI_INTENT, "
            "T9_MODIFY_DRAFT,T10_CANCEL_DRAFT,T0_UNKNOWN。source 必须为 llm_fallback。"
            "entities 的值必须是 {value, certainty}，certainty 只能是 explicit, approximate, inferred, missing。"
            "不得声称用户已确认交易；不得输出 SQL、订单提交命令或工具调用。"
        )
        user = f"conversation_summary={conversation_summary}\nquery={query}"
        response = await self._client.chat.completions.create(
            model=self.settings.qwen_chat_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("empty structured intent response")
        import json
        return json.loads(content)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        # DashScope may reset connections for the OpenAI-compatible endpoint
        # when a batch contains multiple inputs. Send one text per request and
        # preserve ordering; this is slower but deterministic for ingestion.
        vectors: list[list[float]] = []
        logger.info("embedding_started model=%s text_count=%s", self.settings.qwen_embedding_model, len(texts))
        for text in texts:
            response = None
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    response = await self._client.embeddings.create(
                        model=self.settings.qwen_embedding_model,
                        input=text,
                        dimensions=self.settings.embedding_dimension,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning("embedding_retry attempt=%s error_type=%s", attempt, type(exc).__name__)
            if response is None:
                raise last_error or RuntimeError("embedding request failed")
            vectors.append(response.data[0].embedding)
        logger.info("embedding_completed model=%s text_count=%s dimension=%s", self.settings.qwen_embedding_model, len(vectors), len(vectors[0]) if vectors else 0)
        if any(len(vector) != self.settings.embedding_dimension for vector in vectors):
            raise ValueError(f"embedding dimension must be {self.settings.embedding_dimension}")
        return vectors

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
