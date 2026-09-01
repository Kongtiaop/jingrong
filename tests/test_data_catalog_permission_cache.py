from __future__ import annotations

import asyncio

from app.services.data_catalog_service import CatalogSearchScope, DataCatalogService
from app.services.permission_scoped_cache import PermissionContext, PermissionScopedCache


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex):
        self.values[key] = value


class FakeRepository:
    async def search_tables(self, **kwargs): return []
    async def search_examples(self, **kwargs): return []
    async def search_columns(self, **kwargs): return []
    async def relations_for_tables(self, **kwargs): return []


def test_catalog_search_cache_is_permission_scoped():
    async def run():
        backend = FakeRedis()
        service = DataCatalogService(FakeRepository(), permission_cache=PermissionScopedCache(backend))
        first = CatalogSearchScope(role_codes=("risk_manager",), permission_context=PermissionContext(user_id="u1", role_codes=("risk_manager",), permission_codes=("risk:alert_read",)))
        second = CatalogSearchScope(role_codes=("operations",), permission_context=PermissionContext(user_id="u2", role_codes=("operations",), permission_codes=("order:review",)))
        result = await service.search(query="风险预警", scope=first)
        assert result["tables"] == []
        assert len(backend.values) == 1
        await service.search(query="风险预警", scope=second)
        assert len(backend.values) == 2
        cached = await service.search(query="风险预警", scope=first)
        assert cached["retrieval_mode"].endswith("+permission-cache")

    asyncio.run(run())
