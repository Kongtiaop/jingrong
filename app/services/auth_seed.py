from __future__ import annotations

from uuid import uuid4
from sqlalchemy import select
from app.core.settings import Settings
from app.db.session import Database
from app.models.auth import Permission, Role, User
from app.services.auth_service import hash_password

DEMO_ACCOUNTS = [
    ("customer_demo", "customer", "客户演示账号", False),
    ("customer_service_demo", "customer_service", "客服演示账号", False),
    ("product_manager_demo", "product_manager", "产品经理演示账号", False),
    ("risk_manager_demo", "risk_manager", "风控演示账号", False),
    ("operations_demo", "operations", "运营演示账号", False),
    ("auditor_demo", "auditor", "审计演示账号", False),
    ("super_admin_demo", "super_admin", "超级管理员演示账号", True),
]
PERMISSIONS = [
    ("customer:read", "查看客户"), ("customer:write", "管理客户"),
    ("product:read", "查看产品"), ("product:write", "管理产品"),
    ("risk:read", "查看风险"), ("risk:write", "管理风险"),
    ("asset:read", "查看资产"), ("order:read", "查看订单"), ("order:write", "管理订单"),
    ("audit:read", "查看审计"), ("admin:write", "管理系统"),
]
ROLE_PERMISSIONS = {
    "customer": ["customer:read", "product:read", "asset:read", "order:read"],
    "customer_service": ["customer:read", "customer:write", "product:read", "asset:read", "order:read"],
    "product_manager": ["product:read", "product:write"],
    "risk_manager": ["customer:read", "risk:read", "risk:write", "product:read", "order:read"],
    "operations": ["product:read", "asset:read", "order:read", "order:write"],
    "auditor": ["customer:read", "product:read", "risk:read", "asset:read", "order:read", "audit:read"],
    "super_admin": [code for code, _ in PERMISSIONS],
    "employee_pending": [],
}

async def ensure_auth_seed(database: Database, settings: Settings) -> None:
    if not settings.demo_accounts_enabled or settings.app_env not in {"development", "dev", "local"}:
        return
    async with database.session_factory() as session:
        permission_map = {}
        # Seed must be safe under reloads and concurrent startup workers.
        # Flush each newly created permission before the next lookup so the
        # SELECT sees pending rows and does not enqueue duplicate INSERTs.
        for code, name in PERMISSIONS:
            permission = (await session.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
            if permission is None:
                permission = Permission(id=str(uuid4()), code=code, name=name)
                session.add(permission)
                await session.flush()
            elif permission.name != name:
                permission.name = name
            permission_map[code] = permission
        await session.flush()
        role_map = {}
        role_codes = {role_code for _, role_code, _, _ in DEMO_ACCOUNTS} | {"employee_pending"}
        for role_code in role_codes:
            role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one_or_none()
            if role is None:
                role = Role(id=str(uuid4()), code=role_code, name=role_code); session.add(role)
            else:
                await session.refresh(role, attribute_names=["permissions"])
            role.permissions.clear()
            role.permissions.extend(permission_map[code] for code in ROLE_PERMISSIONS[role_code])
            role_map[role_code] = role
        await session.flush()
        for username, role_code, display_name, is_super_admin in DEMO_ACCOUNTS:
            user = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if user is None:
                user = User(id=str(uuid4()), username=username, password_hash=hash_password(f"Demo@2026{role_code.title().replace('_', '')}"), display_name=display_name, is_super_admin=is_super_admin, status="active")
                session.add(user)
            else:
                await session.refresh(user, attribute_names=["roles"])
            user.roles.clear()
            user.roles.append(role_map[role_code])
        await session.commit()
