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
    ("customer_service_e2e", "customer_service", "客服 E2E 账号", False),
    ("customer_manager_demo", "customer_manager", "客户经理演示账号", False),
    ("advisor_demo", "advisor", "理财顾问演示账号", False),
    ("product_manager_demo", "product_manager", "产品经理演示账号", False),
    ("risk_manager_demo", "risk_manager", "风控演示账号", False),
    ("operations_demo", "operations", "运营演示账号", False),
    ("auditor_demo", "auditor", "审计演示账号", False),
    ("super_admin_demo", "super_admin", "超级管理员演示账号", True),
]
PERMISSIONS = [
    ("customer:write", "管理客户"),
    ("customer:read_all", "查看工作人员客户"), ("customer:read_assigned", "查看已授权客户"),
    ("product:read", "查看产品"), ("product:write", "管理产品"),
    ("risk:alert_read", "查看风险预警与 AML 批次"), ("risk:alert_handle", "调查处理风险预警"),
    ("risk:alert_action", "执行风险动作"), ("risk:scan_execute", "执行 AML 扫描"),
    ("asset:read", "查看资产"), ("order:read", "查看订单"),
    ("order:submit_staff", "工作人员代办赎回"), ("transfer:submit_staff", "工作人员代办转账"),
    ("order:review", "审核订单与业务操作"), ("operation:read_staff", "查看工作人员业务操作"),
    ("operation:audit_read", "查看业务操作审计"),
    ("task:create", "创建异步任务"), ("task:read", "查看异步任务"),
    ("workorder:create", "创建工单"), ("workorder:read", "查看工单"),
    ("workorder:handle", "处理/关闭工单"), ("workorder:assign", "分派工单"),
    ("customer_service:accept", "接入实时客服"), ("customer_service:assign", "指定实时客服坐席"),
    ("audit:read", "查看审计"), ("admin:write", "管理系统"),
]
ROLE_PERMISSIONS = {
    "customer": ["product:read", "asset:read", "order:read", "workorder:create", "workorder:read"],
    "customer_service": ["customer:read_all", "customer:write", "product:read", "asset:read", "order:read", "workorder:create", "workorder:read", "workorder:handle", "customer_service:accept"],
    "customer_manager": ["customer:read_all", "customer:write", "product:read", "asset:read", "order:read", "transfer:submit_staff", "workorder:create", "workorder:read", "workorder:handle", "workorder:assign", "customer_service:accept", "customer_service:assign"],
    "advisor": ["customer:read_assigned", "product:read", "asset:read", "order:read", "order:submit_staff"],
    "product_manager": ["product:read", "product:write"],
    "risk_manager": ["customer:read_all", "risk:alert_read", "risk:alert_handle", "risk:alert_action", "risk:scan_execute", "product:read", "order:read", "workorder:create", "workorder:read", "workorder:handle", "workorder:assign", "customer_service:accept", "customer_service:assign"],
    "operations": ["product:read", "asset:read", "order:read", "order:review", "operation:read_staff", "operation:audit_read", "task:create", "task:read", "workorder:create", "workorder:read", "workorder:handle", "workorder:assign", "customer_service:accept", "customer_service:assign"],
    "auditor": ["customer:read_all", "product:read", "risk:alert_read", "asset:read", "order:read", "audit:read", "operation:audit_read", "task:read", "workorder:read"],
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
            user.is_super_admin = is_super_admin
        await session.commit()
