from uuid import uuid4
from app.models.customer_service import CustomerServiceMessage, CustomerServiceSession

class CustomerServiceSessionFactory:
    def __init__(self, session): self.session=session
    async def waiting_human(self, *, customer_id: str, work_order_id: str | None = None, reason: str = "customer_requested") -> CustomerServiceSession:
        row=CustomerServiceSession(id=str(uuid4()), customer_id=customer_id, work_order_id=work_order_id, status="waiting_human", mode="human", handoff_reason=reason)
        self.session.add(row); await self.session.flush(); return row
    async def message(self, *, session_id: str, content: str, sender_type: str = "customer", sender_user_id: str | None = None) -> CustomerServiceMessage:
        row=CustomerServiceMessage(id=str(uuid4()),session_id=session_id,sender_type=sender_type,sender_user_id=sender_user_id,content=content,message_type="text",source="customer_input" if sender_type=="customer" else "human_reply")
        self.session.add(row); await self.session.flush(); return row
