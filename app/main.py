from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.api import api_router
from app.common.exceptions import AppException, app_exception_handler, validation_exception_handler
from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.common.middleware.trace import TraceIdMiddleware
from app.common.middleware.request_guard import RequestGuardMiddleware
from app.core.settings import get_settings
from app.db.health import DatabaseHealth
from app.db.schema import ensure_schema
from app.db.session import Database
from app.infrastructure.qwen import QwenProvider
from app.infrastructure.redis_client import RedisClient
from app.services.health_service import HealthService
from app.services.task_service import TaskService
from app.services.auth_service import AuthService
from app.services.auth_seed import ensure_auth_seed
from app.services.profile_seed import ensure_profile_seed
from app.services.trading_seed import ensure_trading_seed
from app.services.demo_customer_seed import ensure_demo_customer_profiles
from app.services.demo_product_seed import ensure_demo_products
from app.services.trading_service import TradingService
from app.services.data_catalog_seed import ensure_data_catalog_seed
from app.services.understanding_config import understanding_config
from sqlalchemy import text

logger = get_logger(__name__)

# 启动初始化跨进程互斥锁键（任意稳定整数；多 worker 各自实例共用此键）
_STARTUP_LOCK_KEY = 727001

async def _seed_startup_locked(database: Database, settings) -> None:
    """多 worker 下保证 schema DDL + 各 seed 只由一个进程执行、其它等待。

    原因：uvicorn --workers 4 会让每个 worker 都跑一次 lifespan；若不加锁，两个 worker 并行跑
    ensure_schema(一堆 ALTER/CREATE/DROP INDEX，AccessExclusiveLock) + seed SELECT 会产生锁环 →
    PostgreSQL deadlock → 部分 worker 启动崩并反复重启。

    用专用连接持有 session 级 pg_advisory_lock 覆盖整个 schema+seed 段，finally 释放，
    保证同一时刻仅一个 worker 在做 DDL/seed——幂等重跑安全，其它进程等锁通过。
    """
    # 只在引擎可用(PostgreSQL)时才加；engine 无则退化保底直跑
    try:
        async with database.engine.connect() as conn:
            await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _STARTUP_LOCK_KEY})
            try:
                await _run_all_seeds(database, settings)
            finally:
                try:
                    await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _STARTUP_LOCK_KEY})
                except Exception:  # noqa: BLE001 - 释放失败不应二次污染启动
                    pass
    except Exception:  # noqa: BLE001 - 非 PG 或无 advisory 场景：不做并发保护直接跑
        await _run_all_seeds(database, settings)


async def _run_all_seeds(database: Database, settings) -> None:
    await ensure_schema(database.engine)
    await ensure_auth_seed(database, settings)
    await ensure_profile_seed(database)
    await ensure_trading_seed(database)
    await ensure_demo_products(database)
    await ensure_demo_customer_profiles(database)
    await ensure_data_catalog_seed(database)


def _install_rate_limiter(settings, redis_client) -> None:
    """把 Redis 限流器挂到已注册的 Guard 中间件上。

    为什么在 lifespan 里挂：`add_middleware` 发生在**模块导入时**，
    那时 `RedisClient` 还没连接；而 Starlette 把中间件实例存在
    `app.middleware_stack`（首次请求时才构建）。这里直接遍历
    `app.user_middleware` 的 kwargs 改不了已构建实例，故采用
    **模块级可注入引用**：Guard 从 `app.state` 之外拿不到 redis，
    改由本函数把 limiter 实例写到一个模块级槽位，Guard 读该槽位。

    权衡：这比"把 redis 传进中间件构造器"要绕，但避免了在导入期建立连接
    （导入期连接会污染所有测试与 CLI 脚本）。
    """
    if int(getattr(settings, "rate_limit_requests", 0) or 0) <= 0:
        return
    try:
        from app.common.rate_limit import RedisRateLimiter
        from app.common.middleware import request_guard
        request_guard.set_limiter(RedisRateLimiter(redis_client.client))
        logger.info("rate_limiter_enabled limit=%s window_s=%s",
                    settings.rate_limit_requests, settings.rate_limit_window_seconds)
    except Exception as exc:  # noqa: BLE001 - 限流器装不上不阻塞启动
        logger.error("rate_limiter_install_failed err=%s — 限流未生效", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    # 加载理解层配置（难度路由/检索参数/灰度）。此前仅在测试中 reload；
    # 若此处不加载，understanding_config 单例 _data 恒为空 → 全部走代码内置默认。
    understanding_config.reload()
    database = Database(settings)
    redis_client = RedisClient(settings)
    await _seed_startup_locked(database, settings)
    app.state.settings = settings
    app.state.database = database
    app.state.redis = redis_client
    _install_rate_limiter(settings, redis_client)
    app.state.health_service = HealthService(DatabaseHealth(database), redis_client, QwenProvider(settings), settings)
    app.state.task_service = TaskService(database, settings)
    app.state.auth_service = AuthService(database, settings)
    if settings.database_backend.lower() == "mysql":
        raise RuntimeError(
            "DATABASE_BACKEND=mysql is validation-only and cannot serve HTTP trading routes yet; "
            "keep DATABASE_BACKEND=postgresql and run MySQL validation scripts separately"
        )
    app.state.trading_service = TradingService(settings=settings)
    # LangGraph 断点续跑：仅当开关打开才建 Postgres checkpointer（会建表 + 占连接池）。
    # 失败不阻塞启动：图在无 checkpointer 时仍可跑，只是没有跨进程断点。
    # 但必须打 ERROR（而非 warning）：开关开着却没生效属于"静默降级"，运维会误以为断点续跑已启用。
    app.state.agent_checkpointer = None
    if getattr(settings, "agent_checkpoint_enabled", False):
        try:
            from app.agents.checkpointer import conninfo_from_database_url, ensure_postgres_checkpointer
            app.state.agent_checkpointer = await ensure_postgres_checkpointer(
                conninfo_from_database_url(settings.database_url))
            logger.info("agent_checkpointer_ready thread_id=conversation_id")
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "agent_checkpointer_init_failed err=%s: %s — "
                "AGENT_CHECKPOINT_ENABLED=true 但断点续跑未生效，图将以无 checkpoint 模式运行",
                type(exc).__name__, exc,
            )
    try:
        yield
    finally:
        checkpointer = getattr(app.state, "agent_checkpointer", None)
        if checkpointer is not None:
            try:
                await checkpointer.conn.close()
            except Exception:  # noqa: BLE001
                pass
        await redis_client.close()
        await database.dispose()


app = FastAPI(title="Wealth Manager API", version="0.1.0", lifespan=lifespan)

# ---- 中间件注册顺序（Starlette 逆序包裹：**后 add 的在最外层**）----
# 目标执行顺序（外→内）：
#   RequestGuard（限流/体限/超时） → TraceId → CORS → GZip → 路由
# 理由：
#   * Guard 在外层：体限/限流应**先于**业务执行，且被限流的请求也要能观测到；
#   * TraceId 次之：仍能覆盖被 CORS 拒绝/404/401 的请求（实测验证过）；
#   * CORS 在 GZip 之外：CORS 头必须能加到压缩响应上；
#   * GZip 在最内：只压缩业务真实产生的响应。
# 故注册顺序（先 add 的在内层）必须是：GZip → CORS → TraceId → Guard，
# 其中 GZip 由 Guard 内部包住（见 RequestGuardMiddleware._inner），故此处不单独 add。
def _cors_origins() -> list[str]:
    """CORS 白名单改为**配置驱动**（此前硬编码 localhost，生产换域名要改代码）。"""
    raw = getattr(get_settings(), "cors_allow_origins", "") or ""
    return [o.strip() for o in raw.split(",") if o.strip()]


app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(TraceIdMiddleware)
# Guard 最后 add → 处于最外层，最先看到请求、最后写响应。
app.add_middleware(
    RequestGuardMiddleware,
    max_body_bytes=get_settings().request_max_body_bytes,
    timeout_seconds=get_settings().request_timeout_seconds,
    limiter=None,  # lifespan 后才能拿到 redis；由 startup 事件挂上（见下方 _install_rate_limiter）
    rate_limit=get_settings().rate_limit_requests,
    rate_window_seconds=get_settings().rate_limit_window_seconds,
    gzip_min_size=get_settings().gzip_min_size_bytes,
)
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.include_router(api_router)
