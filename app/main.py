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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    redis_client = RedisClient(settings)
    await ensure_schema(database.engine)
    await ensure_auth_seed(database, settings)
    await ensure_profile_seed(database)
    await ensure_trading_seed(database)
    await ensure_demo_products(database)
    await ensure_demo_customer_profiles(database)
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
