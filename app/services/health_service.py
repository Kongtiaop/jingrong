import time

from app.core.settings import Settings
from app.db.health import DatabaseHealth
from app.infrastructure.redis_client import RedisClient
from app.infrastructure.qwen import QwenProvider


class HealthService:
    def __init__(self, database_health: DatabaseHealth, redis_client: RedisClient, qwen: QwenProvider, settings: Settings) -> None:
        self.database_health = database_health
        self.redis = redis_client
        self.qwen = qwen
        self.settings = settings
        # 短 TTL 缓存，避免每次 /health 请求都 ping PG+Redis（高并发下是瓶颈）
        self._cache_ts: float = 0.0
        self._cache: dict[str, dict[str, str]] | None = None

    async def check_all(self) -> dict[str, dict[str, str]]:
        now = time.monotonic()
        if self._cache is not None and now - self._cache_ts < 5.0:
            return self._cache
        result = {
            "postgresql": await self.database_health.check(),
            "redis": await self.redis.check(),
            "qwen": await self.qwen.check_config(),
            "embedding": {
                "status": "ok" if self.settings.embedding_dimension == 1024 else "error",
                "dimension": str(self.settings.embedding_dimension),
                "model": self.settings.qwen_embedding_model,
            },
        }
        self._cache = result
        self._cache_ts = now
        return result
