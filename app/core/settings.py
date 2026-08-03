from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "wealth-manager"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://wealth:wealth_dev@127.0.0.1:5432/wealth_manager"
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_stream_prefix: str = "stream:wealth"
    redis_consumer_group: str = "wealth-workers"
    redis_consumer_name: str = "worker-1"
    database_backend: str = "postgresql"
    vector_store_backend: str = "pgvector"
    database_read_backend: str = "postgresql"
    database_write_backend: str = "postgresql"
    migration_shadow_read: bool = False
    agent_backend: str = "local"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "wealth_documents"
    mysql_url: str = "mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3307/wealth_manager"
    mysql_validation_writes: bool = False

    dashscope_api_key: str = Field(default="", repr=False)
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_chat_model: str = "qwen-plus"
    qwen_embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimension: int = 1024
    embedding_smoke_check: bool = False

    jwt_secret: str = Field(default="change-me-in-development-please-rotate-32", repr=False)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    refresh_token_expire_days: int = 30
    demo_accounts_enabled: bool = True
    employee_registration_code: str = Field(default="WEALTH-EMPLOYEE-DEV", repr=False)
    memory_sync_timeout_ms: int = 2500
    task_max_attempts: int = 3
    task_block_ms: int = 5000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    def validate_p0(self) -> None:
        if self.database_backend.lower() not in {"postgresql", "mysql"}:
            raise ValueError("DATABASE_BACKEND must be postgresql or mysql")
        if self.vector_store_backend.lower() not in {"pgvector", "milvus"}:
            raise ValueError("VECTOR_STORE_BACKEND must be pgvector or milvus")
        if self.database_write_backend.lower() != "postgresql" and not self.mysql_validation_writes:
            raise ValueError("database writes remain PostgreSQL until migration gate is explicitly completed")
        if self.mysql_validation_writes and self.app_env in {"production", "prod"}:
            raise ValueError("MYSQL_VALIDATION_WRITES cannot be enabled in production")
        if self.app_env in {"production", "prod"} and self.demo_accounts_enabled:
            raise ValueError("DEMO_ACCOUNTS_ENABLED must be false in production")
        if self.embedding_dimension != 1024:
            raise ValueError("EMBEDDING_DIMENSION must be 1024")
        if self.memory_sync_timeout_ms <= 0:
            raise ValueError("MEMORY_SYNC_TIMEOUT_MS must be positive")
        if self.task_max_attempts <= 0:
            raise ValueError("TASK_MAX_ATTEMPTS must be positive")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_p0()
    return settings

