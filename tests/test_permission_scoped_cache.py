from __future__ import annotations

import asyncio

from app.services.permission_scoped_cache import PermissionContext, PermissionScopedCache, bump_permission_context_version


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex):
        self.values[key] = value

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value


def test_cache_key_isolated_by_user_and_permission_context():
    cache = PermissionScopedCache(FakeRedis())
    first = PermissionContext(user_id="user-a", role_codes=("risk_manager",), permission_codes=("risk:alert_read",))
    second = PermissionContext(user_id="user-b", role_codes=("risk_manager",), permission_codes=("risk:alert_read",))
    changed_permissions = PermissionContext(user_id="user-a", role_codes=("risk_manager",), permission_codes=())

    first_key = cache.build_key(namespace="data_catalog_search", context=first, scope={"query": "风险预警"})
    assert first_key != cache.build_key(namespace="data_catalog_search", context=second, scope={"query": "风险预警"})
    assert first_key != cache.build_key(namespace="data_catalog_search", context=changed_permissions, scope={"query": "风险预警"})
    assert "user-a" not in first_key
    assert "risk:alert_read" not in first_key


def test_cache_hit_never_crosses_permission_context():
    async def run():
        cache = PermissionScopedCache(FakeRedis())
        first = PermissionContext(user_id="user-a", role_codes=("risk_manager",), permission_codes=("risk:alert_read",))
        second = PermissionContext(user_id="user-b", role_codes=("operations",), permission_codes=("order:review",))
        await cache.set_json(namespace="data_catalog_search", context=first, scope={"query": "风险预警"}, value={"tables": [{"table_name": "risk_alert"}]})
        assert await cache.get_json(namespace="data_catalog_search", context=first, scope={"query": "风险预警"}) == {"tables": [{"table_name": "risk_alert"}]}
        assert await cache.get_json(namespace="data_catalog_search", context=second, scope={"query": "风险预警"}) is None

    asyncio.run(run())


def test_cache_backend_failure_falls_back_to_miss_without_leaking_value():
    class BrokenRedis:
        async def get(self, key):
            raise ConnectionError("redis unavailable")

        async def set(self, key, value, ex):
            raise ConnectionError("redis unavailable")

    async def run():
        cache = PermissionScopedCache(BrokenRedis())
        context = PermissionContext(user_id="user-a")
        assert await cache.get_json(namespace="data_catalog_search", context=context, scope={"query": "风险"}) is None
        assert await cache.set_json(namespace="data_catalog_search", context=context, scope={"query": "风险"}, value={"tables": []}) is False

    asyncio.run(run())


def test_context_versions_invalidate_previous_cache_keys():
    cache = PermissionScopedCache(FakeRedis())
    before = PermissionContext(user_id="user-a", role_codes=("risk_manager",), permission_codes=("risk:alert_read",), data_scope_version="assignment-v1", catalog_policy_version="catalog-v1")
    after_scope = PermissionContext(user_id="user-a", role_codes=("risk_manager",), permission_codes=("risk:alert_read",), data_scope_version="assignment-v2", catalog_policy_version="catalog-v1")
    after_catalog = PermissionContext(user_id="user-a", role_codes=("risk_manager",), permission_codes=("risk:alert_read",), data_scope_version="assignment-v1", catalog_policy_version="catalog-v2")
    assert cache.build_key(namespace="data_catalog_search", context=before, scope={}) != cache.build_key(namespace="data_catalog_search", context=after_scope, scope={})
    assert cache.build_key(namespace="data_catalog_search", context=before, scope={}) != cache.build_key(namespace="data_catalog_search", context=after_catalog, scope={})


def test_bump_permission_context_version_changes_cached_context_key():
    async def run():
        backend = FakeRedis()
        class Role:
            code = "risk_manager"
            permissions = []
        class User:
            id = "user-a"
            roles = [Role()]
        before = await PermissionContext.from_user_cached(User(), backend)
        assert before.data_scope_version == "0"
        assert await bump_permission_context_version(backend, user_id="user-a") == "1"
        after = await PermissionContext.from_user_cached(User(), backend)
        assert after.data_scope_version == "1"
        assert before.fingerprint() != after.fingerprint()

    asyncio.run(run())
