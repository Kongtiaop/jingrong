from uuid import uuid4
from app.models.auth import User

class UserFactory:
    def __init__(self, session): self.session=session
    async def customer(self, *, username: str | None = None) -> User:
        row=User(id=str(uuid4()), username=username or f"test_customer_{uuid4().hex[:8]}", password_hash="test-hash", display_name="测试客户", status="active")
        self.session.add(row); await self.session.flush(); return row
