from datetime import datetime
from pydantic import BaseModel, Field

class ProfileRequest(BaseModel):
    age:int|None=Field(default=None,ge=0,le=150); occupation:str=""; region:str=""; investment_experience_years:int=Field(default=0,ge=0); investment_goal:str="balanced"; investment_horizon_years:int|None=Field(default=None,ge=0,le=100); liquidity_preference:str="medium"
class ProfileResponse(ProfileRequest):
    id:str; user_id:str; source_type:str; created_at:datetime; updated_at:datetime
class AssetSnapshotRequest(BaseModel):
    total_asset:float=Field(ge=0); cash_balance:float=Field(ge=0); investable_asset:float=Field(ge=0); liability:float=Field(ge=0); net_asset:float=Field(ge=0); source_type:str="synthetic"
class AssetSnapshotResponse(AssetSnapshotRequest):
    id:str; user_id:str; snapshot_time:datetime; created_at:datetime


class AssetSnapshotHistoryResponse(BaseModel):
    items: list[AssetSnapshotResponse]
    total: int

class ProductRequest(BaseModel):
    name:str; product_type:str="fund"; target_customer_type:str="individual"; target_customer_tiers:str="ordinary,gold,platinum,diamond,private_bank"; risk_level:str="C1"; term_days:int=0; minimum_amount:float=0; daily_yield_rate:float=Field(default=0,ge=0,le=1); liquidity:str="medium"; description:str=""; status:str="active"
class ProductResponse(ProductRequest):
    id:str; source_type:str; created_at:datetime; updated_at:datetime; deleted_at:datetime|None=None
class ProductListResponse(BaseModel): items:list[ProductResponse]; total:int
class HoldingResponse(BaseModel):
    id: str
    user_id: str
    product_id: str
    product_name: str | None = None
    daily_yield_rate: float = 0
    cumulative_return_rate: float = 0
    quantity: float
    cost_amount: float
    market_value: float
    profit_loss: float
    holding_days: int
    status: str
    created_at: datetime
    updated_at: datetime
class RiskOperationStatusResponse(BaseModel):
    status: str = "normal"
    restriction_status: str = "none"
    restriction_reason: str = ""
    restricted_until: datetime | None = None
    latest_alert_id: str | None = None
    latest_rule_code: str | None = None
    latest_alert_level: str | None = None
    latest_alert_status: str | None = None
    latest_action_code: str | None = None
    latest_action_status: str | None = None

class ProfileSummary(BaseModel):
    profile: ProfileResponse | None
    latest_asset: AssetSnapshotResponse | None
    holdings: list[HoldingResponse]
    risk_operation: RiskOperationStatusResponse = Field(default_factory=RiskOperationStatusResponse)

class SuitabilityResult(BaseModel):
    product_id: str
    product_name: str
    matched: bool
    reasons: list[str]
class CustomerTierResponse(BaseModel):
    user_id: str
    customer_type: str
    customer_tier: str
    investable_asset: float
    reasons: list[str]
class RecommendationResponse(BaseModel):
    user_id: str
    matches: list[SuitabilityResult]
    excluded: list[SuitabilityResult]

class StaffRecommendationRequest(BaseModel):
    customer_id: str

class StaffRecommendationResponse(RecommendationResponse):
    generated_for: str

class AdvisorDraftRequest(BaseModel):
    customer_id: str
    product_id: str
    amount: float = Field(gt=0)
    note: str = ""

class AdvisorDraftRevisionRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)

class AdvisorDraftResponse(BaseModel):
    id: str
    customer_id: str
    advisor_id: str
    product_id: str
    side: str
    amount: float
    status: str
    note: str
    product_name: str = ""
    advisor_name: str = ""
class StaffCustomerListItem(BaseModel):
    id: str
    username: str
    display_name: str
    status: str
    profile: ProfileResponse | None = None
    latest_asset: AssetSnapshotResponse | None = None
    customer_tier: str = "ordinary"
    tier_reasons: list[str] = []
    risk_level: str | None = None
    risk_score: int | None = None
    risk_status: str | None = None
    holding_count: int = 0
    risk_operation_status: str = "normal"
    risk_restriction_status: str = "none"
    risk_restriction_reason: str = ""
    latest_risk_rule_code: str | None = None
    latest_risk_alert_level: str | None = None
class StaffCustomerListResponse(BaseModel):
    items: list[StaffCustomerListItem]
    total: int
class StaffCustomerDetail(ProfileSummary):
    user: StaffCustomerListItem




class EnterpriseVerificationRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=255)
    registration_no: str = Field(default="", max_length=128)
    legal_representative: str = Field(default="", max_length=128)
    contact_phone: str = Field(default="", max_length=64)

class EnterpriseVerificationResponse(EnterpriseVerificationRequest):
    id: str
    user_id: str
    status: str
    review_note: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RiskAssessmentRequest(BaseModel):
    investment_experience_years: int = Field(ge=0, le=80)
    max_loss_tolerance: int = Field(ge=0, le=100)
    investment_horizon_years: int = Field(ge=0, le=100)
    liquidity_need: str = Field(default="medium", pattern="^(high|medium|low)$")
    investment_goal: str = Field(default="balanced", pattern="^(capital_preservation|income|balanced|growth|aggressive)$")
    risk_willingness: int = Field(ge=0, le=100)

class RiskAssessmentResponse(BaseModel):
    id: str
    user_id: str
    risk_level: str
    score: int
    answers: dict
    status: str
    source_type: str
    assessed_at: datetime
    expires_at: datetime | None = None


class AdvisorAuthorizationRequest(BaseModel):
    advisor_id: str
    allowed_actions: str = "buy"
    product_scope: str = "*"
    max_single_amount: float = Field(gt=0)
    max_daily_amount: float = Field(gt=0)
    risk_level_ceiling: str = "C2"
    requires_customer_confirmation: bool = False

class AdvisorAuthorizationResponse(BaseModel):
    id: str
    customer_id: str
    advisor_id: str
    allowed_actions: str
    product_scope: str
    max_single_amount: float
    max_daily_amount: float
    risk_level_ceiling: str
    status: str
    requires_customer_confirmation: bool


class RiskAlertResponse(BaseModel):
    id: str
    customer_id: str
    order_id: str | None
    rule_code: str
    alert_level: str
    status: str
    confidence: float
    evidence: dict
    recommendation: str
    handled_by: str | None
    handled_note: str
    created_at: datetime
    handled_at: datetime | None
    action_code: str | None = None
    action_status: str = "not_started"
    action_note: str = ""
    investigation_id: str | None = None
    report_draft_id: str | None = None
    restriction_status: str = "none"
    restriction_expires_at: datetime | None = None
    customer_name: str = ""
    order_no: str = ""
    product_name: str = ""
    handled_by_name: str = ""
    work_order_id: str | None = None
    work_order_ticket_no: str = ""
    work_order_status: str = ""
    latest_customer_supplement: str = ""
    latest_customer_supplement_at: datetime | None = None

class AmlBatchRunResponse(BaseModel):
    id: str
    batch_key: str
    scan_window_start: datetime
    scan_window_end: datetime
    scan_days: int
    status: str
    total_customers: int
    succeeded_customers: int
    failed_customers: int
    created_alerts: int
    error_summary: str = ""
    started_at: datetime
    finished_at: datetime | None = None
    run_type: str = "daily"
    source_batch_id: str | None = None


class AmlBatchRunListResponse(BaseModel):
    items: list[AmlBatchRunResponse]
    total: int


class AmlSchedulerStatusResponse(BaseModel):
    running: bool
    last_heartbeat_at: datetime | None = None
    last_scan_started_at: datetime | None = None
    last_scan_finished_at: datetime | None = None
    last_scan_status: str = "never"
    last_error: str = ""


class RiskAlertHandleRequest(BaseModel):
    status: str = Field(pattern="^(acknowledged|investigating|pending_customer|report_pending|resolved|dismissed)$")
    note: str = ""
    assessment_result: str | None = Field(default=None, pattern="^(confirmed_risk|cleared_after_review|false_positive|insufficient_information)$")
    assessment_note: str = ""
    customer_consulted: bool = False
    customer_consultation_note: str = ""
    disposition_code: str | None = Field(default=None, pattern="^(HOLD_ORDER|REJECT_ORDER|RESTRICT_TRADING|RELEASE_RESTRICTION|REQUEST_MATERIALS|CREATE_INVESTIGATION|CREATE_REPORT_DRAFT|ESCALATE|NO_ACTION)$")
    disposition_note: str = ""

class RiskAlertActionRequest(BaseModel):
    action_code: str = Field(pattern="^(HOLD_ORDER|REJECT_ORDER|RESTRICT_TRADING|RELEASE_RESTRICTION|CREATE_INVESTIGATION|CREATE_REPORT_DRAFT|REQUEST_MATERIALS|ESCALATE)$")
    note: str = ""



