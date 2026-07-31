from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion  # noqa: F401
from app.models.outbox import OutboxEvent
from app.models.auth import Permission, RefreshSession, Role, User  # noqa: F401
from app.models.task import AsyncTask
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, CustomerSubjectiveProfile, Product, ProductSuitabilityRule, RiskRule  # noqa: F401
from app.models.trading import Account, Order, Trade, OrderStatusHistory  # noqa: F401


async def ensure_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("ALTER TABLE async_task ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
        await connection.execute(text("ALTER TABLE outbox_event ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"))
        await connection.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ"))
        await connection.execute(text("ALTER TABLE product ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ"))
        await connection.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)"))
        await connection.execute(text("""
            DELETE FROM orders duplicate
            USING orders keeper
            WHERE duplicate.user_id = keeper.user_id
              AND duplicate.idempotency_key IS NOT NULL
              AND duplicate.idempotency_key = keeper.idempotency_key
              AND duplicate.id > keeper.id
        """))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_user_idempotency_key ON orders (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_embedding_hnsw ON knowledge_chunk USING hnsw (embedding vector_cosine_ops)"))

