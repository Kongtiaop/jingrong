from fastapi import APIRouter
from app.api.health import router as health_router
from app.api.auth import router as auth_router
from app.api.knowledge import router as knowledge_router
from app.api.tasks import router as tasks_router
from app.api.profile import router as profile_router
from app.api.trading import router as trading_router
from app.api.admin import router as admin_router
from app.api.risk import router as risk_router
from app.api.operations import router as operations_router
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(tasks_router)
api_router.include_router(profile_router)
api_router.include_router(knowledge_router)
api_router.include_router(trading_router)
api_router.include_router(admin_router)
api_router.include_router(risk_router)
api_router.include_router(operations_router)
