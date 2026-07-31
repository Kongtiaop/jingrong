from datetime import datetime
from pydantic import BaseModel, Field

class ProfileRequest(BaseModel):
    age:int|None=Field(default=None,ge=0,le=150); occupation:str=""; region:str=""; customer_type:str="individual"; investment_experience_years:int=Field(default=0,ge=0); investment_goal:str=""; investment_horizon:str=""; liquidity_preference:str=""
class ProfileResponse(ProfileRequest):
    id:str; user_id:str; source_type:str; created_at:datetime; updated_at:datetime
class AssetSnapshotRequest(BaseModel):
    total_asset:float=Field(ge=0); cash_balance:float=Field(ge=0); investable_asset:float=Field(ge=0); liability:float=Field(ge=0); net_asset:float=Field(ge=0); source_type:str="synthetic"
class AssetSnapshotResponse(AssetSnapshotRequest):
    id:str; user_id:str; snapshot_time:datetime; created_at:datetime
class ProductRequest(BaseModel):
    name:str; product_type:str="fund"; risk_level:str="C1"; term_days:int=0; minimum_amount:float=0; liquidity:str="medium"; description:str=""; status:str="active"
class ProductResponse(ProductRequest):
    id:str; source_type:str; created_at:datetime; updated_at:datetime; deleted_at:datetime|None=None
class ProductListResponse(BaseModel): items:list[ProductResponse]; total:int
class HoldingResponse(BaseModel):
    id:str; user_id:str; product_id:str; quantity:float; cost_amount:float; market_value:float; profit_loss:float; holding_days:int; status:str; created_at:datetime; updated_at:datetime
class ProfileSummary(BaseModel):
    profile:ProfileResponse|None; latest_asset:AssetSnapshotResponse|None; holdings:list[HoldingResponse]

class SuitabilityResult(BaseModel):
    product_id: str
    product_name: str
    matched: bool
    reasons: list[str]
class RecommendationResponse(BaseModel):
    user_id: str
    matches: list[SuitabilityResult]
    excluded: list[SuitabilityResult]
class StaffCustomerListItem(BaseModel):
    id: str
    username: str
    display_name: str
    status: str
    profile: ProfileResponse | None = None
    latest_asset: AssetSnapshotResponse | None = None
    holding_count: int = 0
class StaffCustomerListResponse(BaseModel):
    items: list[StaffCustomerListItem]
    total: int
class StaffCustomerDetail(ProfileSummary):
    user: StaffCustomerListItem
