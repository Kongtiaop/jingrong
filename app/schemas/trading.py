from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

class AccountResponse(BaseModel):
    id: str
    account_no: str
    currency: str
    available_balance: Decimal
    frozen_balance: Decimal
    status: str

class OrderCreateRequest(BaseModel):
    product_id: str
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    idempotency_key: str | None = Field(default=None, max_length=128)

class OrderStatusHistoryResponse(BaseModel):
    id: str
    from_status: str | None
    to_status: str
    operator_user_id: str
    note: str
    created_at: datetime

class OrderResponse(BaseModel):
    id: str
    order_no: str
    user_id: str
    product_id: str
    product_name: str | None = None
    amount: Decimal
    quantity: Decimal
    status: str
    side: str
    review_note: str
    failure_reason: str
    order_source: str = "customer_direct"
    advisor_id: str | None = None
    confirmation_mode: str = "customer_order"
    created_at: datetime
    updated_at: datetime
    history: list[OrderStatusHistoryResponse] = []

class OrderActionRequest(BaseModel):
    note: str = ""

class TradeResponse(BaseModel):
    id: str
    trade_no: str
    order_id: str
    product_id: str
    amount: Decimal
    quantity: Decimal
    executed_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int


class NaturalLanguageOrderDraftRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class OrderDraftProductOption(BaseModel):
    id: str
    name: str
    risk_level: str
    minimum_amount: Decimal


class NaturalLanguageOrderDraftResponse(BaseModel):
    status: str
    side: str = "buy"
    product_id: str | None = None
    product_name: str | None = None
    amount: Decimal | None = None
    missing_fields: list[str] = []
    clarification: str | None = None
    suitable: bool | None = None
    suitability_reasons: list[str] = []
    precheck_passed: bool = False
    warnings: list[str] = []
    product_options: list[OrderDraftProductOption] = []
    confirmation_required: bool = True
    executed: bool = False


class NaturalLanguageOrderDraftPersistedResponse(NaturalLanguageOrderDraftResponse):
    draft_id: str
    version: int
    confirmation_token: str
    expires_at: datetime


class NaturalLanguageOrderDraftConfirmRequest(BaseModel):
    confirmation_token: str = Field(min_length=20, max_length=512)
    idempotency_key: str | None = Field(default=None, max_length=128)
