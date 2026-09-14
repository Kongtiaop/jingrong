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
    # RAG 关键词检索后端：tsvector（Postgres 内置全文，现状默认）/ pg_search（ParadeDB BM25）。
    # 切换 pg_search 前必须在目标 Postgres 安装 pg_search 扩展并按其版本执行建索引 SQL（见 scripts/enable_pg_search_bm25.sql）。
    fulltext_backend: str = "tsvector"
    migration_shadow_read: bool = False
    agent_backend: str = "local"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "wealth_documents"
    mysql_url: str = "mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3307/wealth_manager"
    mysql_validation_writes: bool = False

    dashscope_api_key: str = Field(default="", repr=False)
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_chat_model: str = "qwen-plus"
    qwen_max_model: str = "qwen-max"   # LLM-as-judge 用更强模型（judge ≠ 生成模型，减少自评偏置）
    # 交互式 LLM 调用的受控超时：避免 DashScope 偶发抖动时单次请求干挂到 httpx 默认 120s
    qwen_chat_timeout_seconds: float = 30.0
    qwen_chat_connect_timeout_seconds: float = 8.0
    # 与 docker 生产一致：真实 DashScope 通义千问 embedding 模型。旧默认 "qwen3.7-text-embedding"
    # 非真实模型名，曾导致入库向量与 API 查询向量模型不一致（向量检索失效）。必须与重嵌入用同一模型。
    qwen_embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    embedding_smoke_check: bool = False
    llm_intent_fallback_enabled: bool = False
    llm_intent_fallback_allowlist: str = "T1_INFORMATION_QUERY,T2_PRODUCT_RECOMMENDATION,T7_HUMAN_HANDOFF"
    llm_intent_shadow_enabled: bool = False
    llm_intent_fallback_timeout_seconds: float = 8.0
    llm_intent_fallback_max_concurrency: int = 4

    # LangGraph Supervisor 真图接线（渐进式三开关，默认全关 → 生产仍走确定性管线）
    # agent_graph_enabled：API 真正改走 SupervisorGraph 编译图（会替换管线执行）。
    agent_graph_enabled: bool = False
    # agent_graph_shadow_enabled：管线照常返回用户，图在后台并行跑一遍，差异写审计。切流前的对照期用。
    agent_graph_shadow_enabled: bool = False
    # agent_checkpoint_enabled：把图状态持久化到 Postgres（断点续跑）；thread_id = conversation_id。
    agent_checkpoint_enabled: bool = False
    # ReAct 预算：必须与管线 CustomerTaskExecutor.MAX_REACT_ITERATIONS / MAX_REACT_TOOL_CALLS
    # （均 20）对齐。此前图默认 4/8，导致同一 query 两条路径的检索尝试次数不同
    # （实测图 t1=4 次、t2=5 次 vs 管线各 2 次），"图 vs 管线"双跑对照本身不公平
    # （ISSUE-20260914-REACT-BUDGET-AND-PLACEHOLDER-DIVERGENCE）。
    agent_graph_max_iterations: int = 20
    agent_graph_max_tool_calls: int = 20
    # 只读查询的**模型自主工具调度**（function calling）：由模型决定调哪个只读工具、传什么参数、
    # 是否再调一轮。资金/风控仍走确定性路径（本开关只影响只读工具选择）。
    # 关闭时回退到图的确定性 ReAct（工具名硬编码 + 固定模板改写）。
    agent_model_tool_selection_enabled: bool = False
    # 模型工具循环的最大轮数（防无限轮）；过大会拉高延迟，建议 2~4。
    agent_model_tool_max_rounds: int = 4

    jwt_secret: str = Field(default="change-me-in-development-please-rotate-32", repr=False)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    refresh_token_expire_days: int = 30
    demo_accounts_enabled: bool = True
    employee_registration_code: str = Field(default="WEALTH-EMPLOYEE-DEV", repr=False)
    memory_sync_timeout_ms: int = 2500
    task_max_attempts: int = 3
    task_block_ms: int = 5000
    aml_scheduler_enabled: bool = False
    aml_scheduler_poll_seconds: int = 60
    aml_scheduler_hour_utc: int = 1
    aml_scheduler_minute_utc: int = 10
    aml_scheduler_days: int = 7
    customer_service_timeout_worker_enabled: bool = False
    customer_service_timeout_worker_poll_seconds: int = 30
    customer_service_heartbeat_timeout_seconds: int = 90
    customer_service_waiting_timeout_seconds: int = 1800
    customer_service_waiting_cleanup_enabled: bool = True
    workorder_upload_dir: str = "uploads/workorders"
    workorder_attachment_max_bytes: int = 10 * 1024 * 1024

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
        if self.fulltext_backend.lower() not in {"tsvector", "pg_search"}:
            raise ValueError("FULLTEXT_BACKEND must be tsvector or pg_search")
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
        if self.aml_scheduler_poll_seconds <= 0:
            raise ValueError("AML_SCHEDULER_POLL_SECONDS must be positive")
        if not 0 <= self.aml_scheduler_hour_utc <= 23:
            raise ValueError("AML_SCHEDULER_HOUR_UTC must be between 0 and 23")
        if not 0 <= self.aml_scheduler_minute_utc <= 59:
            raise ValueError("AML_SCHEDULER_MINUTE_UTC must be between 0 and 59")
        if not 1 <= self.aml_scheduler_days <= 30:
            raise ValueError("AML_SCHEDULER_DAYS must be between 1 and 30")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_p0()
    return settings



