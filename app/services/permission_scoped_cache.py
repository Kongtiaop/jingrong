from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PermissionContext:
    """All authorization dimensions that can affect a cached read result."""

    user_id: str
    role_codes: tuple[str, ...] = ()
    permission_codes: tuple[str, ...] = ()
    tenant_id: str | None = None
    data_scope_version: str = "v1"
    catalog_policy_version: str = "v1"

    @classmethod
    async def from_user_cached(cls, user, backend, *, tenant_id: str | None = None, catalog_policy_version: str = "v1") -> "PermissionContext":
        roles = tuple(sorted({role.code for role in user.roles}))
        permissions = tuple(sorted({permission.code for role in user.roles for permission in role.permissions}))
        version_key = f"wm:permission-context-version:{user.id}"
        try:
            version = await backend.get(version_key) or "0"
            if isinstance(version, bytes):
                version = version.decode("utf-8")
        except Exception:
            version = "0"
        data_scope_version = str(version)
        try:
            subject_scope_version = await backend.get(f"wm:subject-scope-version:{user.id}")
            if subject_scope_version is not None:
                if isinstance(subject_scope_version, bytes):
                    subject_scope_version = subject_scope_version.decode("utf-8")
                data_scope_version = f"{data_scope_version}:subject-{subject_scope_version}"
        except Exception:
            # Live authorization checks remain authoritative if cache version reads fail.
            pass
        return cls(user_id=str(user.id), role_codes=roles, permission_codes=permissions, tenant_id=tenant_id, data_scope_version=data_scope_version, catalog_policy_version=catalog_policy_version)

    @classmethod
    def from_user(cls, user, *, tenant_id: str | None = None, data_scope_version: str = "v1", catalog_policy_version: str = "v1") -> "PermissionContext":
        roles = tuple(sorted({role.code for role in user.roles}))
        permissions = tuple(sorted({permission.code for role in user.roles for permission in role.permissions}))
        return cls(user_id=str(user.id), role_codes=roles, permission_codes=permissions, tenant_id=tenant_id, data_scope_version=data_scope_version, catalog_policy_version=catalog_policy_version)

    def fingerprint(self) -> str:
        payload = {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "roles": self.role_codes,
            "permissions": self.permission_codes,
            "data_scope_version": self.data_scope_version,
            "catalog_policy_version": self.catalog_policy_version,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class PermissionScopedCache:
    """Authorization-aware cache facade. Sensitive reads never accept caller-built raw keys."""

    def __init__(self, backend, *, key_prefix: str = "wm") -> None:
        self.backend = backend
        self.key_prefix = key_prefix

    @staticmethod
    def _scope_fingerprint(scope: Mapping[str, Any] | None) -> str:
        canonical = json.dumps(scope or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    def build_key(self, *, namespace: str, context: PermissionContext, scope: Mapping[str, Any] | None = None) -> str:
        if not namespace or ":" in namespace:
            raise ValueError("namespace must be a non-empty logical name without ':'")
        return f"{self.key_prefix}:{namespace}:pc-{context.fingerprint()}:sc-{self._scope_fingerprint(scope)}"

    async def get_json(self, *, namespace: str, context: PermissionContext, scope: Mapping[str, Any] | None = None) -> dict | list | None:
        key = self.build_key(namespace=namespace, context=context, scope=scope)
        try:
            value = await self.backend.get(key)
            if value is None:
                return None
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            return json.loads(value)
        except Exception:
            # Cache is an optimization only. A backend failure must never change authorization behavior.
            return None

    async def set_json(self, *, namespace: str, context: PermissionContext, scope: Mapping[str, Any] | None = None, value: dict | list, ttl_seconds: int = 60) -> bool:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        key = self.build_key(namespace=namespace, context=context, scope=scope)
        try:
            await self.backend.set(key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), ex=ttl_seconds)
            return True
        except Exception:
            return False


async def bump_permission_context_version(backend, *, user_id: str) -> str:
    """Invalidate all permission-scoped cache namespaces for a user by changing their context version."""
    key = f"wm:permission-context-version:{user_id}"
    try:
        value = await backend.incr(key)
        return str(value)
    except Exception:
        # Security remains correct because live roles/permissions stay in the fingerprint; this only loses eager invalidation.
        return "v1"


async def bump_subject_scope_version(backend, *, user_id: str) -> str:
    """Invalidate cached reads whose result can depend on an advisor's customer assignments."""
    key = f"wm:subject-scope-version:{user_id}"
    try:
        value = await backend.incr(key)
        return str(value)
    except Exception:
        # Live SubjectScopeService checks remain authoritative; this only loses eager cache invalidation.
        return "v1"

