from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models.memory import CustomerMemory, CustomerMemoryEvent


class CustomerMemoryService:
    def __init__(self, database, redis_client=None, ttl_seconds: int = 1800):
        self.database = database
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds

    def _key(self, customer_id: str, conversation_id: str) -> str:
        return f"customer:memory:short:{customer_id}:{conversation_id}"

    async def load_short_term(self, *, customer_id: str, conversation_id: str) -> dict:
        if self.redis is None:
            return {}
        raw = await self.redis.get(self._key(customer_id, conversation_id))
        if not raw:
            return {}
        return json.loads(raw)

    async def save_short_term(self, *, customer_id: str, conversation_id: str, state: dict) -> None:
        if self.redis is None:
            return
        await self.redis.set(self._key(customer_id, conversation_id), json.dumps(state, ensure_ascii=False), ex=self.ttl_seconds)

    async def clear_short_term(self, *, customer_id: str, conversation_id: str) -> None:
        if self.redis is not None:
            await self.redis.delete(self._key(customer_id, conversation_id))

    async def remember(self, *, customer_id: str, key: str, value: str, memory_type: str = "conversation_fact", source: str = "conversation", confidence: float = 1.0, conversation_id: str | None = None, scope: str = "private", agent_type: str = "customer_service") -> CustomerMemory:
        async with self.database.session_factory() as session:
            # 去重包含 scope：私有与共享同 key 可各自存在
            row = (await session.execute(select(CustomerMemory).where(
                CustomerMemory.customer_id == customer_id, CustomerMemory.key == key,
                CustomerMemory.scope == scope, CustomerMemory.status == "active"))).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if row is None:
                row = CustomerMemory(id=str(uuid4()), customer_id=customer_id, key=key, value=value,
                                     memory_type=memory_type, source=source, confidence=confidence,
                                     last_confirmed_at=now, scope=scope, agent_type=agent_type)
                session.add(row); await session.flush()
            else:
                row.value=value; row.source=source; row.confidence=confidence; row.last_confirmed_at=now
                row.scope = scope; row.agent_type = agent_type
            session.add(CustomerMemoryEvent(id=str(uuid4()), customer_id=customer_id, conversation_id=conversation_id, memory_id=row.id, event_type="upsert", payload={"key":key,"value":value,"source":source,"confidence":confidence}))
            await session.commit(); await session.refresh(row)
            return row

    async def load_long_term(self, *, customer_id: str, keys: list[str] | None = None) -> list[CustomerMemory]:
        async with self.database.session_factory() as session:
            query=select(CustomerMemory).where(CustomerMemory.customer_id == customer_id, CustomerMemory.status == "active")
            if keys: query=query.where(CustomerMemory.key.in_(keys))
            return list((await session.execute(query.order_by(CustomerMemory.last_confirmed_at.desc()))).scalars().all())


    async def update_short_term(self, *, customer_id: str, conversation_id: str, patch: dict) -> dict:
        state = await self.load_short_term(customer_id=customer_id, conversation_id=conversation_id)
        state.update(patch)
        await self.save_short_term(customer_id=customer_id, conversation_id=conversation_id, state=state)
        return state

    async def remember_confirmed_parameter(self, *, customer_id: str, key: str, value: str, conversation_id: str | None = None) -> CustomerMemory:
        return await self.remember(customer_id=customer_id, key=key, value=value, memory_type="confirmed_parameter", source="customer_confirmed", confidence=1.0, conversation_id=conversation_id)
