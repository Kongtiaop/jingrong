from __future__ import annotations

from app.repositories.trading import SqlAlchemyTradingUnitOfWork
from app.repositories.mysql_profile import (
    MySQLRepository, MySQLTransaction, MySQLUsersAdapter, MySQLProfilesAdapter,
    MySQLRisksAdapter, MySQLEnterpriseVerificationAdapter, MySQLAuditLogAdapter, MySQLProductsAdapter, MySQLAccountsAdapter,
    MySQLOrdersAdapter, MySQLHoldingsAdapter, MySQLTradesAdapter, MySQLCustomerQueryAdapter,
    MySQLStatusHistoryAdapter, MySQLAssetsAdapter,
)
from app.repositories.contracts import TradingUnitOfWorkProtocol


def create_postgres_unit_of_work():
    return SqlAlchemyTradingUnitOfWork()


class MySQLServiceUnitOfWork:
    """Validation-only UoW with TradingService-compatible read adapters."""

    def __init__(self, mysql_url: str):
        self.database = MySQLRepository(mysql_url)
        self.transaction_factory = self.transaction
        self.users = MySQLUsersAdapter(self.database)
        self.admin = self.users
        self.customer_queries = MySQLCustomerQueryAdapter(self.database)
        self.profiles = MySQLProfilesAdapter(self.database)
        self.enterprise_verifications = MySQLEnterpriseVerificationAdapter(self.database)
        self.audit_logs = MySQLAuditLogAdapter(self.database)
        self.risks = MySQLRisksAdapter(self.database)
        self.products = MySQLProductsAdapter(self.database)
        self.accounts = MySQLAccountsAdapter(self.database)
        self.orders = MySQLOrdersAdapter(self.database)
        self.holdings = MySQLHoldingsAdapter(self.database)
        self.assets = MySQLAssetsAdapter(self.database)
        self.trades = MySQLTradesAdapter(self.database)
        self.status_history = MySQLStatusHistoryAdapter(self.database)

    def transaction(self):
        return MySQLTransaction(self.database)

    async def close(self):
        await self.database.close()


def create_mysql_validation_unit_of_work(mysql_url: str):
    return MySQLServiceUnitOfWork(mysql_url)


def create_configured_unit_of_work(settings):
    backend = settings.database_backend.lower()
    if backend == "postgresql":
        return create_postgres_unit_of_work()
    if backend == "mysql":
        return create_mysql_validation_unit_of_work(settings.mysql_url)
    raise ValueError(f"unsupported database backend: {backend}")
