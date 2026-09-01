from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.db.session import Database
from app.models.bad_case import BadCase


class BadCaseRepository:
    """bad case 落库与查询（数据飞轮"积累"落库）。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert_by_hash(self, *, customer_id: str | None, query: str, query_hash: str,
                             intent: str, confidence: float | None, evidence_status: str,
                             handoff: bool, clarifications: int, reasons: list[str]) -> bool:
        """按 query_hash 去重落库；已存在则跳过。返回是否新插入。"""
        async with self.database.session_factory() as session:
            existing = (await session.execute(select(BadCase.id).where(BadCase.query_hash == query_hash))).scalar_one_or_none()
            if existing:
                return False
            session.add(BadCase(
                id=str(uuid4()), customer_id=customer_id, query=query, query_hash=query_hash,
                intent=intent or "", confidence=confidence, evidence_status=evidence_status or "",
                handoff=bool(handoff), clarifications=int(clarifications or 0), reasons=list(reasons or []),
                status="pending",
            ))
            await session.commit()
            return True

    async def list_pending(self, limit: int = 100) -> list[BadCase]:
        async with self.database.session_factory() as session:
            result = await session.execute(
                select(BadCase).where(BadCase.status == "pending").order_by(BadCase.created_at).limit(limit))
            return list(result.scalars().all())

    async def mark_reviewed(self, bad_case_id: str) -> None:
        from sqlalchemy import update
        async with self.database.session_factory() as session:
            await session.execute(update(BadCase).where(BadCase.id == bad_case_id).values(status="reviewed"))
            await session.commit()
