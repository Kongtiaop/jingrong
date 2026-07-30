from typing import Annotated
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, desc, func, or_
from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_permission, require_staff_permission
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, Product
from app.schemas.profile import *
from app.services.suitability_service import SuitabilityService

router=APIRouter(prefix="/profile",tags=["profile"])
@router.get("/me",response_model=ApiResponse[ProfileSummary])
async def my_summary(request:Request,user:Annotated[User,Depends(current_user)]):
 async with request.app.state.database.session_factory() as s:
  profile=(await s.execute(select(CustomerProfile).where(CustomerProfile.user_id==user.id))).scalar_one_or_none()
  asset=(await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id).order_by(desc(CustomerAssetSnapshot.snapshot_time)).limit(1))).scalar_one_or_none()
  holdings=list((await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user.id))).scalars().all())
  return ApiResponse(data=ProfileSummary(profile=ProfileResponse.model_validate(profile,from_attributes=True) if profile else None,latest_asset=AssetSnapshotResponse.model_validate(asset,from_attributes=True) if asset else None,holdings=[HoldingResponse.model_validate(h,from_attributes=True) for h in holdings]))
@router.put("/me",response_model=ApiResponse[ProfileResponse])
async def update_profile(request:Request,payload:ProfileRequest,user:Annotated[User,Depends(current_user)]):
 async with request.app.state.database.session_factory() as s:
  obj=(await s.execute(select(CustomerProfile).where(CustomerProfile.user_id==user.id))).scalar_one_or_none()
  if obj is None: obj=CustomerProfile(id=str(uuid4()),user_id=user.id);s.add(obj)
  for k,v in payload.model_dump().items():setattr(obj,k,v)
  await s.commit();await s.refresh(obj);return ApiResponse(data=ProfileResponse.model_validate(obj,from_attributes=True))
@router.post("/me/assets",response_model=ApiResponse[AssetSnapshotResponse],status_code=201)
async def create_asset(request:Request,payload:AssetSnapshotRequest,user:Annotated[User,Depends(current_user)]):
 async with request.app.state.database.session_factory() as s:
  obj=CustomerAssetSnapshot(id=str(uuid4()),user_id=user.id,**payload.model_dump());s.add(obj);await s.commit();await s.refresh(obj);return ApiResponse(data=AssetSnapshotResponse.model_validate(obj,from_attributes=True))
@router.get("/products",response_model=ApiResponse[list[ProductResponse]])
async def products(request:Request):
 async with request.app.state.database.session_factory() as s:
  rows=list((await s.execute(select(Product).where(Product.status=="active").order_by(Product.name))).scalars().all());return ApiResponse(data=[ProductResponse.model_validate(x,from_attributes=True) for x in rows])
@router.get("/staff/customers", response_model=ApiResponse[StaffCustomerListResponse])
async def staff_customers(request: Request, q: str = "", limit: int = 50, offset: int = 0, user: User = Depends(require_staff_permission("customer:read"))):
    limit = min(max(limit, 1), 100)
    async with request.app.state.database.session_factory() as s:
        query = select(User).where(User.status == "active", User.roles.any(code="customer"))
        if q.strip():
            needle = f"%{q.strip()}%"
            query = query.where(or_(User.username.ilike(needle), User.display_name.ilike(needle)))
        total = (await s.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        users = list((await s.execute(query.order_by(User.created_at).offset(offset).limit(limit))).scalars().all())
        items = []
        for customer in users:
            profile = (await s.execute(select(CustomerProfile).where(CustomerProfile.user_id == customer.id))).scalar_one_or_none()
            asset = (await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id == customer.id).order_by(desc(CustomerAssetSnapshot.snapshot_time)).limit(1))).scalar_one_or_none()
            holding_count = (await s.execute(select(func.count()).select_from(CustomerHolding).where(CustomerHolding.user_id == customer.id))).scalar_one()
            items.append(StaffCustomerListItem(id=customer.id, username=customer.username, display_name=customer.display_name, status=customer.status, profile=ProfileResponse.model_validate(profile, from_attributes=True) if profile else None, latest_asset=AssetSnapshotResponse.model_validate(asset, from_attributes=True) if asset else None, holding_count=holding_count))
        return ApiResponse(data=StaffCustomerListResponse(items=items, total=total))

@router.get("/staff/customer/{user_id}",response_model=ApiResponse[ProfileSummary])
async def staff_summary(request:Request,user_id:str,user: User = Depends(require_staff_permission("customer:read"))):
 async with request.app.state.database.session_factory() as s:
  profile=(await s.execute(select(CustomerProfile).where(CustomerProfile.user_id==user_id))).scalar_one_or_none();asset=(await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user_id).order_by(desc(CustomerAssetSnapshot.snapshot_time)).limit(1))).scalar_one_or_none();holdings=list((await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user_id))).scalars().all());return ApiResponse(data=ProfileSummary(profile=ProfileResponse.model_validate(profile,from_attributes=True) if profile else None,latest_asset=AssetSnapshotResponse.model_validate(asset,from_attributes=True) if asset else None,holdings=[HoldingResponse.model_validate(h,from_attributes=True) for h in holdings]))

@router.get("/me/recommendations", response_model=ApiResponse[RecommendationResponse])
async def recommendations(request: Request, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as session:
        data = await SuitabilityService().recommend(session, str(user.id))
        return ApiResponse(data=data)
