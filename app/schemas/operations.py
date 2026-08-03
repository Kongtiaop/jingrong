from decimal import Decimal
from pydantic import BaseModel, Field
class OperationResponse(BaseModel):
    operation_id: str
    operation: str
    status: str
    amount: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    product_id: str|None = None
    to_account: str|None = None

class ContactUpdateRequest(BaseModel):
    region: str|None=None
    occupation: str|None=None
class RedeemRequest(BaseModel):
    idempotency_key: str|None = Field(default=None,max_length=128)
    product_id:str
    quantity:Decimal=Field(gt=0)
    amount:Decimal=Field(gt=0)
class TransferRequest(BaseModel):
    idempotency_key: str|None = Field(default=None,max_length=128)
    to_account:str=Field(min_length=1,max_length=128)
    amount:Decimal=Field(gt=0)
    confirmed:bool=False

class OperationReviewRequest(BaseModel):
    approve: bool
    note: str = ""

class StaffRedeemRequest(RedeemRequest):
    customer_id: str
    customer_confirmed: bool = False


class StaffTransferRequest(TransferRequest):
    customer_id: str
    customer_confirmed: bool = False
