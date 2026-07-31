from typing import Annotated
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, desc, func, or_
from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_permission, require_staff_permission, staff_customer_user
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerHolding, CustomerProfile, Product
from app.models.audit import AuditLog
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
@router.get("/staff/products", response_model=ApiResponse[ProductListResponse])
async def staff_products(
    request: Request,
    user: Annotated[User, Depends(require_permission("product:write"))],
    q: str = "",
    status: str = "active",
    limit: int = 20,
    offset: int = 0,
):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    async with request.app.state.database.session_factory() as s:
        query = select(Product)
        if status == "active": query = query.where(Product.deleted_at.is_(None), Product.status != "deleted")
        elif status == "deleted": query = query.where(Product.deleted_at.is_not(None), Product.status == "deleted")
        if q.strip():
            needle = f"%{q.strip()}%"
            query = query.where(or_(Product.name.ilike(needle), Product.product_type.ilike(needle)))
        if status not in {"active", "deleted", "all"} and status.strip():
            query = query.where(Product.status == status.strip())
        total = (await s.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        rows = list((await s.execute(query.order_by(Product.name).offset(offset).limit(limit))).scalars().all())
        return ApiResponse(data=ProductListResponse(items=[ProductResponse.model_validate(row, from_attributes=True) for row in rows], total=total))

@router.post("/staff/products", response_model=ApiResponse[ProductResponse], status_code=201)
async def create_staff_product(request: Request, payload: ProductRequest, user: Annotated[User, Depends(require_permission("product:write"))]):
    async with request.app.state.database.session_factory() as s:
        product = Product(id=str(uuid4()), **payload.model_dump())
        s.add(product)
        await s.commit()
        await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.put("/staff/products/{product_id}", response_model=ApiResponse[ProductResponse])
async def update_staff_product(request: Request, product_id: str, payload: ProductRequest, user: Annotated[User, Depends(require_permission("product:write"))]):
    async with request.app.state.database.session_factory() as s:
        product = (await s.execute(select(Product).where(Product.id == product_id, Product.deleted_at.is_(None)))).scalar_one_or_none()
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        for key, value in payload.model_dump().items():
            setattr(product, key, value)
        await s.commit()
        await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.delete("/staff/products/{product_id}", response_model=ApiResponse[ProductResponse])
async def delete_staff_product(request: Request, product_id: str, user: Annotated[User, Depends(require_permission("product:write"))]):
    async with request.app.state.database.session_factory() as s:
        product = (await s.execute(select(Product).where(Product.id == product_id, Product.deleted_at.is_(None)))).scalar_one_or_none()
        if product is None: raise HTTPException(status_code=404, detail="product not found")
        product.status="deleted"; product.deleted_at=datetime.now(timezone.utc)
        s.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="soft_delete", resource_type="product", resource_id=product.id, detail=product.name))
        await s.commit(); await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.put("/staff/products/{product_id}/restore", response_model=ApiResponse[ProductResponse])
async def restore_staff_product(request: Request, product_id: str, user: Annotated[User, Depends(require_permission("product:write"))]):
    async with request.app.state.database.session_factory() as s:
        product = (await s.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if product is None: raise HTTPException(status_code=404, detail="product not found")
        product.status="active"; product.deleted_at=None
        s.add(AuditLog(id=str(uuid4()), actor_user_id=user.id, action="restore", resource_type="product", resource_id=product.id, detail=product.name))
        await s.commit(); await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.get("/products",response_model=ApiResponse[list[ProductResponse]])
async def products(request:Request):
 async with request.app.state.database.session_factory() as s:
  rows=list((await s.execute(select(Product).where(Product.status=="active", Product.deleted_at.is_(None)).order_by(Product.name))).scalars().all());return ApiResponse(data=[ProductResponse.model_validate(x,from_attributes=True) for x in rows])
@router.get("/staff/customers", response_model=ApiResponse[StaffCustomerListResponse])
async def staff_customers(
    request: Request,
    user: Annotated[User, Depends(staff_customer_user)],
    q: str = "",
    limit: int = 50,
    offset: int = 0,
):
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
async def staff_summary(request:Request,user_id:str,user: Annotated[User, Depends(staff_customer_user)]):
 async with request.app.state.database.session_factory() as s:
  profile=(await s.execute(select(CustomerProfile).where(CustomerProfile.user_id==user_id))).scalar_one_or_none();asset=(await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user_id).order_by(desc(CustomerAssetSnapshot.snapshot_time)).limit(1))).scalar_one_or_none();holdings=list((await s.execute(select(CustomerHolding).where(CustomerHolding.user_id==user_id))).scalars().all());return ApiResponse(data=ProfileSummary(profile=ProfileResponse.model_validate(profile,from_attributes=True) if profile else None,latest_asset=AssetSnapshotResponse.model_validate(asset,from_attributes=True) if asset else None,holdings=[HoldingResponse.model_validate(h,from_attributes=True) for h in holdings]))

@router.get("/me/recommendations", response_model=ApiResponse[RecommendationResponse])
async def recommendations(request: Request, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as session:
        data = await SuitabilityService().recommend(session, str(user.id))
        return ApiResponse(data=data)








