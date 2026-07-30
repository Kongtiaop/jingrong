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
