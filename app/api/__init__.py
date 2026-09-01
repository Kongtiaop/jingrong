from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.annotation import router as annotation_router
from app.api.auth import router as auth_router
from app.api.customer_rag import router as customer_rag_router
from app.api.customer_service import router as customer_service_router
from app.api.data_catalog import router as data_catalog_router
from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.notifications import router as notifications_router
from app.api.operations import router as operations_router
from app.api.profile import router as profile_router
from app.api.risk import router as risk_router
from app.api.tasks import router as tasks_router
from app.api.trading import router as trading_router
from app.api.workorders import router as workorders_router


api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(tasks_router)
api_router.include_router(profile_router)
api_router.include_router(knowledge_router)
api_router.include_router(customer_rag_router)
api_router.include_router(customer_service_router)
api_router.include_router(data_catalog_router)
api_router.include_router(trading_router)
api_router.include_router(admin_router)
api_router.include_router(annotation_router)
api_router.include_router(risk_router)
api_router.include_router(operations_router)
api_router.include_router(workorders_router)
api_router.include_router(notifications_router)
