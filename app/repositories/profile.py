class SqlAlchemyUserRepository:
    async def get(self, session, user_id):
        from app.models.auth import User
        return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    async def is_customer(self, session, user_id):
        from sqlalchemy import exists
        from app.models.auth import User
        return bool((await session.execute(select(exists().where(User.id == user_id, User.roles.any(code="customer"))))).scalar())

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.auth import User
from app.models.profile import CustomerProfile, CustomerRiskAssessment, Product

class SqlAlchemyCustomerRepository:
    """Compatibility facade; canonical implementation lives in trading.py."""
    def __new__(cls):
        from app.repositories.trading import SqlAlchemyProfileRepository
        return SqlAlchemyProfileRepository()

class SqlAlchemyProductRepository:
    """Compatibility facade; canonical implementation lives in trading.py."""
    def __new__(cls):
        from app.repositories.trading import SqlAlchemyProductReadRepository
        return SqlAlchemyProductReadRepository()

class SqlAlchemyRiskAssessmentRepository:
    """Compatibility facade; canonical implementation lives in trading.py."""
    def __new__(cls):
        from app.repositories.trading import SqlAlchemyRiskRepository
        return SqlAlchemyRiskRepository()
