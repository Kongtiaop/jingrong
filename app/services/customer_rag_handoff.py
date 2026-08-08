from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models.customer_service import CustomerServiceSession
from app.models.workorder import WorkOrder
from app.models.workorder_update import WorkOrderUpdate
from app.services.notification_service import create_workorder_notification


class CustomerRagHandoffService:
    def __init__(self, database):
        self.database = database

    async def handoff(self, *, customer_id: str, query: str, intent: str, evidence_status: str, citations: list[dict] | None = None) -> dict:
        normalized = " ".join(query.lower().split())
        key = hashlib.sha256(f"{intent}:{normalized}".encode()).hexdigest()
        context = {"source": "customer_rag", "intent": intent, "query": query, "evidence_status": evidence_status, "query_key": key, "citations": citations or [], "created_at": datetime.now(timezone.utc).isoformat()}
        async with self.database.session_factory() as session:
            rows = list((await session.execute(select(WorkOrder).where(WorkOrder.customer_id == customer_id, WorkOrder.category == "customer_service", WorkOrder.status.in_(["open", "assigned", "in_progress", "pending_customer"])).order_by(WorkOrder.created_at.desc()).limit(20))).scalars().all())
            existing = next((row for row in rows if json.loads(row.context_json or "{}").get("query_key") == key), None)
            if existing:
                session.add(WorkOrderUpdate(id=str(uuid4()), work_order_id=existing.id, author_user_id=customer_id, author_role="customer_rag", content="客服助手再次收到相同问题，已复用该工单。"))
                work_order = existing
                created = False
            else:
                work_order = WorkOrder(id=str(uuid4()), ticket_no=f"WO{datetime.now(timezone.utc):%Y%m%d%H%M%S}{uuid4().hex[:6].upper()}", customer_id=customer_id, created_by=customer_id, category="customer_service", priority="normal", status="open", title="客服知识不足，需要人工回复", description=query, context_json=json.dumps(context, ensure_ascii=False))
                session.add(work_order); await session.flush()
                await create_workorder_notification(session, user_id=customer_id, notification_type="customer_rag_handoff", title="问题已转人工客服", content=f"问题已转人工处理，工单号：{work_order.ticket_no}", work_order=work_order)
                created = True
            service_session = (await session.execute(select(CustomerServiceSession).where(CustomerServiceSession.customer_id == customer_id, CustomerServiceSession.work_order_id == work_order.id, CustomerServiceSession.status.in_(["waiting_human", "paused", "human_active"])).order_by(CustomerServiceSession.created_at.desc()).limit(1))).scalars().first()
            session_created = False
            if service_session is None:
                service_session = CustomerServiceSession(id=str(uuid4()), customer_id=customer_id, work_order_id=work_order.id, status="waiting_human", mode="human", handoff_reason="rag_low_confidence")
                session.add(service_session); session_created = True
            await session.commit()
            return {"work_order_id": work_order.id, "ticket_no": work_order.ticket_no, "created": created, "status": work_order.status, "customer_service_session_id": service_session.id, "customer_service_session_created": session_created, "customer_service_session_status": service_session.status}
