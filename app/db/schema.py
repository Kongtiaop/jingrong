from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base
from app.models.audit import AuditLog  # noqa: F401
from app.models.auth import CustomerAdvisorAuthorization, Permission, RefreshSession, Role, User  # noqa: F401
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentVersion  # noqa: F401
from app.models.data_catalog import DataCatalogColumn, DataCatalogDomain, DataCatalogExampleQuery, DataCatalogRelation, DataCatalogRevision, DataCatalogTable  # noqa: F401
from app.models.outbox import OutboxEvent
from app.models.profile import CustomerAssetSnapshot, CustomerEnterpriseVerification, CustomerHolding, CustomerProfile, CustomerRiskAssessment, CustomerSubjectiveProfile, Product, ProductSuitabilityRule, RiskRule  # noqa: F401
from app.models.task import AsyncTask
from app.models.workorder import WorkOrder  # noqa: F401
from app.models.workorder_update import WorkOrderUpdate  # noqa: F401
from app.models.workorder_attachment import WorkOrderAttachment  # noqa: F401
from app.models.notification import Notification  # noqa: F401
from app.models.trading import Account, Order, Trade, OrderStatusHistory, AdvisorOperationDraft, MoneyFlow, RiskAlert, RiskInvestigation, RiskReportDraft, AmlBatchRun, AmlBatchAlert, AmlSchedulerState  # noqa: F401


async def ensure_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        statements = (
            "ALTER TABLE async_task ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE outbox_event ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE notification ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(160)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS target_customer_type VARCHAR(64) NOT NULL DEFAULT 'individual'",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS daily_yield_rate NUMERIC(12,8) NOT NULL DEFAULT 0",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS target_customer_tiers TEXT NOT NULL DEFAULT 'ordinary,gold,platinum,diamond,private_bank'",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS annual_income NUMERIC(18,2)",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS investment_horizon VARCHAR(64) NOT NULL DEFAULT '3-5年'",
            "UPDATE customer_profile SET investment_horizon='3-5年' WHERE investment_horizon IS NULL",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS investment_horizon_years INTEGER",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS liquidity_preference VARCHAR(16) NOT NULL DEFAULT 'medium'",
            "ALTER TABLE customer_risk_assessment ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS customer_tier VARCHAR(64) NOT NULL DEFAULT 'ordinary'",
            "ALTER TABLE customer_asset_snapshot ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_holding ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_holding ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product_suitability_rule ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product_suitability_rule ADD COLUMN IF NOT EXISTS minimum_customer_tier VARCHAR(64) NOT NULL DEFAULT 'ordinary'",
            "ALTER TABLE product_suitability_rule ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE risk_rule ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE risk_rule ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)",
            "CREATE INDEX IF NOT EXISTS ix_money_flow_customer_occurred_at ON money_flow (customer_id, occurred_at)",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS assessment_result VARCHAR(32)",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS assessment_note TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS customer_consulted BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS customer_consultation_note TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS disposition_code VARCHAR(64)",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS disposition_note TEXT NOT NULL DEFAULT ''",            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS action_code VARCHAR(64)",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS action_status VARCHAR(32) NOT NULL DEFAULT 'not_started'",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS action_note TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS investigation_id VARCHAR(36)",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS report_draft_id VARCHAR(36)",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS restriction_status VARCHAR(32) NOT NULL DEFAULT 'none'",
            "ALTER TABLE risk_alert ADD COLUMN IF NOT EXISTS restriction_expires_at TIMESTAMPTZ",
            "ALTER TABLE account ADD COLUMN IF NOT EXISTS risk_restriction_status VARCHAR(32) NOT NULL DEFAULT 'none'",
            "ALTER TABLE account ADD COLUMN IF NOT EXISTS risk_restriction_reason TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE account ADD COLUMN IF NOT EXISTS risk_restricted_until TIMESTAMPTZ",
            "ALTER TABLE customer_service_session ADD COLUMN IF NOT EXISTS agent_last_heartbeat_at TIMESTAMPTZ",
            "CREATE INDEX IF NOT EXISTS ix_customer_service_session_agent_last_heartbeat_at ON customer_service_session (agent_last_heartbeat_at)",
            "ALTER TABLE work_order ADD COLUMN IF NOT EXISTS risk_alert_id VARCHAR(36)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_work_order_risk_alert_id ON work_order (risk_alert_id) WHERE risk_alert_id IS NOT NULL",
            "ALTER TABLE work_order ADD COLUMN IF NOT EXISTS context_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE advisor_operation_draft ADD COLUMN IF NOT EXISTS recommendation_snapshot_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE advisor_operation_draft ADD COLUMN IF NOT EXISTS evidence_status VARCHAR(32) NOT NULL DEFAULT 'unavailable'",
            "ALTER TABLE advisor_operation_draft ADD COLUMN IF NOT EXISTS rag_citations_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE advisor_operation_draft ADD COLUMN IF NOT EXISTS customer_revision_note TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE advisor_operation_draft ADD COLUMN IF NOT EXISTS revision_requested_at TIMESTAMPTZ",
            "ALTER TABLE aml_batch_run ADD COLUMN IF NOT EXISTS run_type VARCHAR(32) NOT NULL DEFAULT 'daily'",
            "ALTER TABLE aml_batch_run ADD COLUMN IF NOT EXISTS source_batch_id VARCHAR(36)",
            "CREATE INDEX IF NOT EXISTS ix_aml_batch_run_source_batch_id ON aml_batch_run (source_batch_id)",

            "CREATE INDEX IF NOT EXISTS ix_users_status_created_at ON users (status, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_user_roles_role_user ON user_roles (role_id, user_id)",
        )
        for statement in statements:
            await connection.execute(text(statement))
        await connection.execute(text("""
            DELETE FROM orders duplicate
            USING orders keeper
            WHERE duplicate.user_id = keeper.user_id
              AND duplicate.idempotency_key IS NOT NULL
              AND duplicate.idempotency_key = keeper.idempotency_key
              AND duplicate.id > keeper.id
        """))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_user_idempotency_key ON orders (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL"))
        await connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_user_idempotency ON notification (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_embedding_hnsw ON knowledge_chunk USING hnsw (embedding vector_cosine_ops)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_data_catalog_table_embedding_hnsw ON data_catalog_table USING hnsw (embedding vector_cosine_ops)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_data_catalog_column_embedding_hnsw ON data_catalog_column USING hnsw (embedding vector_cosine_ops)"))
        await connection.execute(text("CREATE INDEX IF NOT EXISTS ix_data_catalog_example_query_embedding_hnsw ON data_catalog_example_query USING hnsw (embedding vector_cosine_ops)"))












