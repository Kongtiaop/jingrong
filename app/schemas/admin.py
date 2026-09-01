from datetime import datetime
from pydantic import BaseModel, Field
class AdminUserResponse(BaseModel):
    id:str; username:str; display_name:str; status:str; is_super_admin:bool; roles:list[str]; created_at:datetime; updated_at:datetime; deleted_at:datetime|None=None
class AdminUserListResponse(BaseModel):
    items:list[AdminUserResponse]; total:int
class AdminRoleResponse(BaseModel):
    id:str; code:str; name:str; permissions:list[str]
class AdminPermissionResponse(BaseModel):
    id:str; code:str; name:str
class AdminRoleUpdateRequest(BaseModel):
    roles:list[str]=Field(min_length=1)
class AdminRolePermissionsUpdateRequest(BaseModel):
    permissions: list[str] = Field(default_factory=list)


class EnterpriseVerificationReviewRequest(BaseModel):
    approved: bool
    note: str = ""


class EnterpriseVerificationListResponse(BaseModel):
    items: list[dict]
    total: int


class OutboxEventResponse(BaseModel):
    id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str | None
    status: str
    attempt: int
    error_message: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    payload_json: dict


class OutboxEventListResponse(BaseModel):
    items: list[OutboxEventResponse]
    total: int
    counts: dict[str, int]


class OutboxRetryResponse(BaseModel):
    id: str
    status: str
    attempt: int

class AdminAdvisorAuthorizationUpsertRequest(BaseModel):
    advisor_id: str
    customer_id: str
    allowed_actions: str = "read"
    product_scope: str = "*"
    max_single_amount: float = Field(ge=0)
    max_daily_amount: float = Field(ge=0)
    risk_level_ceiling: str = "C2"
    requires_customer_confirmation: bool = False


class AdminAdvisorAuthorizationBatchUpdateRequest(BaseModel):
    grants: list[AdminAdvisorAuthorizationUpsertRequest] = Field(default_factory=list)
    revoke_ids: list[str] = Field(default_factory=list)

class IntentShadowMetricsResponse(BaseModel):
    total: int
    errors: int
    template_matches: int
    template_mismatches: int
    model_trade_candidates: int
    unknown_rule_trade_blocked: int
    verified_customer_confirmation_required: int
    recent: list[dict] = Field(default_factory=list)

