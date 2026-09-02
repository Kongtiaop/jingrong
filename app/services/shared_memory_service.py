# -*- coding: utf-8 -*-
"""多 Agent 共享记忆语义层（MVP）。

让一个 Agent 的观察可被其它 Agent 安全复用：
- 写入：`remember_observation` 按 Agent 类型的**可写作用域**校验，落库 `customer_memory`（带 scope/agent_type）并写审计。
- 召回：`recall` 按 Agent 类型的**可读作用域**校验，返回该客户下允许的作用域记忆。

安全边界（沿用 ISSUE-20260814）：
- 只存低风险、白名单 memory_type 的观察；不共享模型私有推理链/原始敏感数据。
- 记忆只做建议，不绕过权限/风控/确认门禁。
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.memory import CustomerMemory
from app.services.memory_service import CustomerMemoryService

# 各 Agent 类型可写/可读的作用域（private=仅写入者；shared=同客户所有 Agent；organization=组织级）
AGENT_MEMORY_PERMS: dict[str, dict[str, set[str]]] = {
    "customer_service": {"write": {"private", "shared"}, "read": {"private", "shared"}},
    "customer_recommendation": {"write": {"private"}, "read": {"shared", "private"}},
    "customer_risk": {"write": {"private"}, "read": {"shared", "private"}},
    "customer_trade": {"write": {"private"}, "read": {"shared", "private"}},
    "advisor": {"write": {"private", "shared"}, "read": {"private", "shared", "organization"}},
    "risk": {"write": {"private", "shared"}, "read": {"private", "shared", "organization"}},
    "business": {"write": {"private"}, "read": {"private", "organization"}},
    "data_analysis": {"write": {"shared"}, "read": {"shared", "organization"}},
}

VALID_SCOPES = {"private", "shared", "organization"}
VALID_MEMORY_TYPES = {"observation", "aggregated_insight", "confirmed_parameter", "conversation_fact"}


class SharedMemoryService:
    def __init__(self, database, redis_client=None):
        self.memory = CustomerMemoryService(database, redis_client)
        self.database = database

    @staticmethod
    def can_access(agent_type: str, scope: str, action: str = "read") -> bool:
        perms = AGENT_MEMORY_PERMS.get(agent_type)
        return bool(perms and scope in perms.get(action, set()))

    async def remember(self, *, customer_id: str | None, agent_type: str, key: str, value: str,
                       memory_type: str = "observation", scope: str = "shared",
                       source: str = "agent_observation", confidence: float = 1.0,
                       conversation_id: str | None = None, actor_user_id: str | None = None) -> CustomerMemory:
        scope = scope or "shared"
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid memory scope: {scope}")
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"invalid memory_type: {memory_type}")
        if scope != "private" and not customer_id:
            raise ValueError("shared/organization memory requires customer_id")
        if not self.can_access(agent_type, scope, "write"):
            raise PermissionError(f"agent {agent_type} cannot write scope {scope}")

        row = await self.memory.remember(
            customer_id=customer_id, key=key, value=value, memory_type=memory_type,
            source=source, confidence=confidence, conversation_id=conversation_id,
            scope=scope, agent_type=agent_type,
        )
        if actor_user_id:
            async with self.database.session_factory() as session:
                session.add(AuditLog(
                    id=str(uuid4()), actor_user_id=actor_user_id,
                    action="shared_memory.write", resource_type="customer_memory", resource_id=str(row.id),
                    detail=f"agent={agent_type};scope={scope};type={memory_type};key={key}",
                ))
                await session.commit()
        return row

    async def recall(self, *, customer_id: str, agent_type: str, scope: str | None = None,
                     memory_type: str | None = None, key: str | None = None,
                     limit: int = 50) -> list[CustomerMemory]:
        allowed = AGENT_MEMORY_PERMS.get(agent_type, {}).get("read", set())
        if scope is not None and scope not in allowed:
            raise PermissionError(f"agent {agent_type} cannot read scope {scope}")
        scopes = [scope] if scope else sorted(allowed & {"private", "shared", "organization"})
        query = select(CustomerMemory).where(
            CustomerMemory.customer_id == customer_id,
            CustomerMemory.status == "active",
            CustomerMemory.scope.in_(scopes),
        )
        if memory_type:
            query = query.where(CustomerMemory.memory_type == memory_type)
        if key:
            query = query.where(CustomerMemory.key == key)
        query = query.order_by(CustomerMemory.last_confirmed_at.desc()).limit(limit)
        async with self.database.session_factory() as session:
            return list((await session.execute(query)).scalars().all())
