from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
class BusinessOperation(Base):
    __tablename__='business_operation'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    operator_user_id: Mapped[str|None]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'),nullable=True,index=True)
    authorization_id: Mapped[str|None]=mapped_column(ForeignKey('customer_advisor_authorization.id',ondelete='SET NULL'),nullable=True,index=True)
    confirmation_mode: Mapped[str]=mapped_column(String(32),default='customer')
    operation_type: Mapped[str]=mapped_column(String(32),index=True)
    status: Mapped[str]=mapped_column(String(32),default='accepted',index=True)
    idempotency_key: Mapped[str|None]=mapped_column(String(128),nullable=True,index=True)
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2),default=0)
    quantity: Mapped[Decimal]=mapped_column(Numeric(18,4),default=0)
    product_id: Mapped[str|None]=mapped_column(ForeignKey('product.id',ondelete='SET NULL'),nullable=True,index=True)
    to_account: Mapped[str|None]=mapped_column(String(128),nullable=True)
    payload_json: Mapped[str]=mapped_column(Text,default='{}')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),index=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),onupdate=func.now())
