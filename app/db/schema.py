from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion  # noqa: F401
from app.models.outbox import OutboxEvent
from app.models.auth import Permission, RefreshSession, Role, User  # noqa: F401  # noqa: F401
from app.models.task import AsyncTask
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, CustomerSubjectiveProfile, Product, ProductSuitabilityRule, RiskRule  # noqa: F401  # noqa: F401


async def ensure_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("ALTER TABLE async_task ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
        await connection.execute(text("ALTER TABLE outbox_event ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
        await connection.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_idempotency_key ON orders (idempotency_key)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_embedding_hnsw ON knowledge_chunk USING hnsw (embedding vector_cosine_ops)"))

from app.models.trading import Account, Order, Trade, OrderStatusHistory  # noqa: F401
