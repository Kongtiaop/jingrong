from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.api import api_router
from app.common.exceptions import AppException, app_exception_handler, validation_exception_handler
from app.common.logging import configure_logging
from app.common.middleware.trace import TraceIdMiddleware
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
    app.state.health_service = HealthService(DatabaseHealth(database), redis_client, QwenProvider(settings), settings)
    app.state.task_service = TaskService(database, settings)
    app.state.auth_service = AuthService(database, settings)
    if settings.database_backend.lower() == "mysql":
        raise RuntimeError(
            "DATABASE_BACKEND=mysql is validation-only and cannot serve HTTP trading routes yet; "
            "keep DATABASE_BACKEND=postgresql and run MySQL validation scripts separately"
        )
    app.state.trading_service = TradingService(settings=settings)
    try:
        yield
    finally:
        await redis_client.close()
        await database.dispose()


app = FastAPI(title="Wealth Manager API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(TraceIdMiddleware)
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.include_router(api_router)
