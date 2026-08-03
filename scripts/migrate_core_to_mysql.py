from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal

import aiomysql
from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import Database
from app.models.auth import User
from app.models.profile import (
    CustomerAssetSnapshot,
    CustomerEnterpriseVerification,
    CustomerHolding,
    CustomerProfile,
    CustomerRiskAssessment,
    Product,
    ProductSuitabilityRule,
    RiskRule,
)
from app.models.trading import Account, Order, OrderStatusHistory, Trade

MYSQL = dict(host="127.0.0.1", port=3307, user="wealth", password="wealth_dev", db="wealth_manager")

DDL = {
    "users": """CREATE TABLE IF NOT EXISTS users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(128) NOT NULL UNIQUE, display_name VARCHAR(128) NOT NULL, status VARCHAR(20) NULL DEFAULT 'active', password_hash TEXT NULL, is_super_admin BOOLEAN NULL DEFAULT 0, created_at DATETIME NULL, updated_at DATETIME NULL, deleted_at DATETIME NULL)""",
    "customer_profile": """CREATE TABLE IF NOT EXISTS customer_profile (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL UNIQUE, age INT NULL, occupation VARCHAR(128) NOT NULL DEFAULT '', region VARCHAR(128) NOT NULL DEFAULT '', customer_type VARCHAR(64) NOT NULL DEFAULT 'individual', customer_tier VARCHAR(64) NOT NULL DEFAULT 'ordinary', investment_experience_years INT NOT NULL DEFAULT 0, investment_goal VARCHAR(64) NOT NULL DEFAULT 'balanced', investment_horizon_years INT NULL, liquidity_preference VARCHAR(16) NOT NULL DEFAULT 'medium', created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "customer_enterprise_verification": """CREATE TABLE IF NOT EXISTS customer_enterprise_verification (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL UNIQUE, company_name VARCHAR(255) NOT NULL, registration_no VARCHAR(128) NOT NULL DEFAULT '', legal_representative VARCHAR(128) NOT NULL DEFAULT '', contact_phone VARCHAR(64) NOT NULL DEFAULT '', status VARCHAR(32) NOT NULL DEFAULT 'pending', review_note TEXT NULL, reviewed_by VARCHAR(36) NULL, reviewed_at DATETIME NULL, created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "product": """CREATE TABLE IF NOT EXISTS product (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE, product_type VARCHAR(64) NOT NULL DEFAULT 'fund', risk_level VARCHAR(16) NOT NULL DEFAULT 'C1', target_customer_type VARCHAR(64) NOT NULL DEFAULT 'individual', target_customer_tiers TEXT NULL, minimum_amount DECIMAL(18,2) NOT NULL DEFAULT 0, liquidity VARCHAR(64) NOT NULL DEFAULT 'medium', description TEXT NULL, status VARCHAR(32) NOT NULL DEFAULT 'active', source_type VARCHAR(32) NULL DEFAULT 'synthetic', created_at DATETIME NULL, updated_at DATETIME NULL, deleted_at DATETIME NULL)""",
    "product_suitability_rule": """CREATE TABLE IF NOT EXISTS product_suitability_rule (id VARCHAR(36) PRIMARY KEY, product_id VARCHAR(36) NOT NULL, minimum_risk_level VARCHAR(16) NULL, investor_type VARCHAR(64) NULL, minimum_investable_asset DECIMAL(18,2) NULL, rule_text TEXT NULL, created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "risk_rule": """CREATE TABLE IF NOT EXISTS risk_rule (id VARCHAR(36) PRIMARY KEY, rule_code VARCHAR(64) NULL UNIQUE, rule_key VARCHAR(128) NULL UNIQUE, name VARCHAR(255) NULL, rule_type VARCHAR(64) NULL, config_json TEXT NULL, risk_level VARCHAR(32) NULL, enabled TINYINT(1) NULL DEFAULT 1, version VARCHAR(32) NULL, source_document VARCHAR(255) NULL, created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "customer_risk_assessment": """CREATE TABLE IF NOT EXISTS customer_risk_assessment (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL UNIQUE, risk_level VARCHAR(8) NOT NULL DEFAULT 'C1', score INT NOT NULL DEFAULT 0, answers_json JSON NULL, status VARCHAR(32) NOT NULL DEFAULT 'provisional', source_type VARCHAR(32) NULL DEFAULT 'system_default', assessed_at DATETIME NULL, expires_at DATETIME NULL, created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "customer_asset_snapshot": """CREATE TABLE IF NOT EXISTS customer_asset_snapshot (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, total_asset DECIMAL(18,2) NOT NULL DEFAULT 0, cash_balance DECIMAL(18,2) NOT NULL DEFAULT 0, investable_asset DECIMAL(18,2) NOT NULL DEFAULT 0, liability DECIMAL(18,2) NOT NULL DEFAULT 0, net_asset DECIMAL(18,2) NOT NULL DEFAULT 0, snapshot_time DATETIME NULL, source_type VARCHAR(32) NULL DEFAULT 'synthetic', created_at DATETIME NULL)""",
    "customer_holding": """CREATE TABLE IF NOT EXISTS customer_holding (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, product_id VARCHAR(36) NOT NULL, quantity DECIMAL(18,4) NOT NULL DEFAULT 0, cost_amount DECIMAL(18,2) NOT NULL DEFAULT 0, market_value DECIMAL(18,2) NOT NULL DEFAULT 0, profit_loss DECIMAL(18,2) NOT NULL DEFAULT 0, holding_days INT NOT NULL DEFAULT 0, status VARCHAR(32) NOT NULL DEFAULT 'active', created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "account": """CREATE TABLE IF NOT EXISTS account (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36) NOT NULL UNIQUE, account_no VARCHAR(64) NOT NULL UNIQUE, currency VARCHAR(8) NOT NULL DEFAULT 'CNY', available_balance DECIMAL(18,2) NOT NULL DEFAULT 0, frozen_balance DECIMAL(18,2) NOT NULL DEFAULT 0, status VARCHAR(32) NOT NULL DEFAULT 'active', created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "orders": """CREATE TABLE IF NOT EXISTS orders (id VARCHAR(36) PRIMARY KEY, order_no VARCHAR(64) NOT NULL UNIQUE, user_id VARCHAR(36) NOT NULL, account_id VARCHAR(36) NOT NULL, product_id VARCHAR(36) NOT NULL, amount DECIMAL(18,2) NOT NULL, quantity DECIMAL(18,4) NOT NULL DEFAULT 0, status VARCHAR(32) NOT NULL DEFAULT 'pending_confirmation', side VARCHAR(16) NOT NULL DEFAULT 'buy', review_note TEXT NULL, failure_reason TEXT NULL, idempotency_key VARCHAR(128) NULL, created_at DATETIME NULL, updated_at DATETIME NULL, UNIQUE KEY ux_orders_user_idempotency (user_id, idempotency_key))""",
    "trades": """CREATE TABLE IF NOT EXISTS trades (id VARCHAR(36) PRIMARY KEY, trade_no VARCHAR(64) NOT NULL UNIQUE, order_id VARCHAR(36) NOT NULL UNIQUE, user_id VARCHAR(36) NOT NULL, product_id VARCHAR(36) NOT NULL, amount DECIMAL(18,2) NOT NULL, quantity DECIMAL(18,4) NOT NULL, executed_at DATETIME NULL)""",
    "order_status_history": """CREATE TABLE IF NOT EXISTS order_status_history (id VARCHAR(36) PRIMARY KEY, order_id VARCHAR(36) NOT NULL, from_status VARCHAR(32) NULL, to_status VARCHAR(32) NOT NULL, operator_user_id VARCHAR(36) NOT NULL, note TEXT NOT NULL, created_at DATETIME NULL)""",
    "audit_logs": """CREATE TABLE IF NOT EXISTS audit_logs (id VARCHAR(36) PRIMARY KEY, actor_user_id VARCHAR(36) NULL, action VARCHAR(64) NOT NULL, resource_type VARCHAR(64) NOT NULL, resource_id VARCHAR(64) NOT NULL, detail TEXT NOT NULL, created_at DATETIME NULL)""",
    "async_task": """CREATE TABLE IF NOT EXISTS async_task (id VARCHAR(36) PRIMARY KEY, task_type VARCHAR(100) NOT NULL, aggregate_type VARCHAR(64) NOT NULL, aggregate_id VARCHAR(36) NULL, idempotency_key VARCHAR(128) NULL UNIQUE, status VARCHAR(32) NOT NULL, attempt INT NOT NULL DEFAULT 0, max_attempts INT NOT NULL DEFAULT 3, payload_json JSON NULL, result_json JSON NULL, error_message TEXT NULL, available_at DATETIME NULL, started_at DATETIME NULL, finished_at DATETIME NULL, created_at DATETIME NULL, updated_at DATETIME NULL)""",
    "outbox_event": """CREATE TABLE IF NOT EXISTS outbox_event (id VARCHAR(36) PRIMARY KEY, event_type VARCHAR(100) NOT NULL, aggregate_type VARCHAR(64) NOT NULL, aggregate_id VARCHAR(36) NULL, payload_json JSON NULL, status VARCHAR(20) NULL, attempt INT NOT NULL DEFAULT 0, error_message TEXT NULL, published_at DATETIME NULL, created_at DATETIME NULL, updated_at DATETIME NULL)""",
}


def value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, Decimal):
        return value
    return value


async def normalize_legacy_asset_columns(cur) -> None:
    await cur.execute("SHOW COLUMNS FROM customer_asset_snapshot")
    existing = {row[0] for row in await cur.fetchall()}
    mappings = {
        "total_assets": "total_asset", "cash_assets": "cash_balance",
        "investment_assets": "investable_asset", "liabilities": "liability",
        "net_assets": "net_asset",
    }
    for old, new in mappings.items():
        if old in existing and new not in existing:
            await cur.execute(f"ALTER TABLE customer_asset_snapshot CHANGE COLUMN `{old}` `{new}` DECIMAL(18,2) NOT NULL DEFAULT 0")
        elif old in existing and new in existing:
            await cur.execute(f"UPDATE customer_asset_snapshot SET `{new}`=`{old}` WHERE (`{new}` IS NULL OR `{new}`=0) AND `{old}` IS NOT NULL")
            await cur.execute(f"ALTER TABLE customer_asset_snapshot DROP COLUMN `{old}`")


async def ensure_table_columns(cur, table: str, required_columns: dict[str, str] | None = None) -> None:
    await cur.execute("SHOW COLUMNS FROM `%s`" % table)
    existing = {row[0] for row in await cur.fetchall()}
    for column, definition in (required_columns or {}).items():
        if column not in existing:
            await cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")


async def insert_rows(cur, table: str, columns: list[str], rows: list[tuple], mysql_version: tuple[int,int,int]) -> None:
    if not rows:
        return
    names = ",".join(f"`{column}`" for column in columns)
    placeholders = ",".join(["%s"] * len(columns))
    if mysql_version >= (8, 0, 19):
        updates = ",".join(f"`{column}`=new.`{column}`" for column in columns if column not in {"id"})
        sql = f"INSERT INTO `{table}` ({names}) VALUES ({placeholders}) AS new ON DUPLICATE KEY UPDATE {updates}"
    else:
        updates = ",".join(f"`{column}`=VALUES(`{column}`)" for column in columns if column not in {"id"})
        sql = f"INSERT INTO `{table}` ({names}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"
    await cur.executemany(sql, [[value(item) for item in row] for row in rows])


async def main() -> None:
    settings = get_settings()
    database = Database(settings)
    conn = await aiomysql.connect(**MYSQL, autocommit=True)
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT VERSION()")
            raw_version = str((await cur.fetchone())[0]).split("-")[0]
            mysql_version = tuple(int(part) for part in raw_version.split(".")[:3])
            await cur.execute("SHOW TABLES")
            existing_tables = {row[0] for row in await cur.fetchall()}
            for table, ddl in DDL.items():
                if table not in existing_tables:
                    await cur.execute(ddl)
                await ensure_table_columns(cur, table)
            await normalize_legacy_asset_columns(cur)
            await ensure_table_columns(cur, "account", {"created_at": "DATETIME NULL"})
            # Keep the validation schema aligned with the PostgreSQL customer-list path.
            # MySQL 8 permits IF NOT EXISTS for indexes; duplicate existing indexes are ignored.
            for index_name, table, columns in [
                ("ix_users_status_created_at", "users", "status, created_at"),
                ("ix_user_roles_role_user", "user_roles", "role_id, user_id"),
            ]:
                await cur.execute("SHOW TABLES LIKE %s", (table,))
                if await cur.fetchone() is None:
                    continue
                await cur.execute("SHOW INDEX FROM `%s`" % table)
                existing_indexes = {row[2] for row in await cur.fetchall()}
                if index_name not in existing_indexes:
                    await cur.execute(f"CREATE INDEX `{index_name}` ON `{table}` ({columns})")
        async with database.session_factory() as session:
            models = [
                ("users", User),
                ("customer_profile", CustomerProfile),
                ("customer_enterprise_verification", CustomerEnterpriseVerification),
                ("product", Product),
                ("product_suitability_rule", ProductSuitabilityRule),
                ("risk_rule", RiskRule),
                ("customer_risk_assessment", CustomerRiskAssessment),
                ("customer_asset_snapshot", CustomerAssetSnapshot),
                ("customer_holding", CustomerHolding),
                ("account", Account),
                ("orders", Order),
                ("trades", Trade),
                ("order_status_history", OrderStatusHistory),
            ]
            async with conn.cursor() as cur:
                for table, model in models:
                    columns = [column for column in model.__table__.columns if column.name != "embedding"]
                    required = {}
                    for column in columns:
                        if column.name in {"id"}:
                            continue
                        type_name = column.type.__class__.__name__
                        if "Boolean" in type_name:
                            definition = "BOOLEAN NOT NULL DEFAULT 0"
                        elif "Integer" in type_name:
                            definition = "INT NULL"
                        elif "Numeric" in type_name:
                            definition = "DECIMAL(18,4) NULL"
                        elif "JSON" in type_name:
                            definition = "JSON NULL"
                        elif "DateTime" in type_name:
                            definition = "DATETIME NULL"
                        elif "Text" in type_name:
                            definition = "TEXT NULL"
                        else:
                            definition = "VARCHAR(255) NULL"
                        required[column.name] = definition
                    for optional_text in {"target_customer_tiers", "description", "review_note", "failure_reason", "allowed_customer_types"}:
                        if optional_text in required:
                            required[optional_text] = "TEXT NULL"
                    await ensure_table_columns(cur, table, required)
                    rows = list((await session.execute(select(model))).scalars().all())
                    if not rows:
                        continue
                    names = [column.name for column in columns]
                    tuples = [tuple(getattr(row, name) for name in names) for row in rows]
                    await insert_rows(cur, table, names, tuples, mysql_version)
        from scripts.validate_mysql_schema import validate
        schema_result = await validate()
        if not schema_result["pass"]:
            raise RuntimeError(f"MySQL schema validation failed: {schema_result}")
        print("core migration completed", {"tables": len(DDL), "mysql_version": mysql_version, "schema_validation": "PASS"})
    finally:
        conn.close()
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
