from typing import Annotated
import json
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.common.exceptions import ConflictError, ResourceNotFoundError
from app.schemas.profile import StaffRecommendationRequest, StaffRecommendationResponse, AdvisorDraftRequest, AdvisorDraftResponse, AdvisorAuthorizationRequest, AdvisorAuthorizationResponse
from app.models.auth import CustomerAdvisorAuthorization
from app.models.trading import Account, AdvisorOperationDraft, Order, RiskAlert
from sqlalchemy import select, desc, func, or_
from app.common.response import ApiResponse
from app.common.security.auth import advisor_user, current_user, require_permission, require_staff_permission, staff_customer_user
from app.models.auth import User
from app.models.profile import CustomerAssetSnapshot, CustomerEnterpriseVerification, CustomerHolding, CustomerProfile, CustomerRiskAssessment, Product
from app.models.audit import AuditLog
from app.schemas.profile import *
from app.services.suitability_service import SuitabilityService
from app.services.customer_tier_service import CustomerTierService, calculate_customer_tier
from app.services.risk_assessment_service import RiskAssessmentService
from app.services.asset_summary_service import AssetSummaryService
from app.services.holding_valuation_service import HoldingValuationService
from app.services.trading_service import TradingError
from app.services.advisor_authorization_service import AdvisorAuthorizationService
from app.services.memory_service import CustomerMemoryService
from app.services.natural_language_order_draft import NaturalLanguageOrderDraftService

router=APIRouter(prefix="/profile",tags=["profile"])

def _advisor_draft_response(draft, product_name: str = "", advisor_name: str = "") -> AdvisorDraftResponse:
    return AdvisorDraftResponse(
        id=draft.id, customer_id=draft.customer_id, advisor_id=draft.advisor_id, product_id=draft.product_id,
        side=draft.side, amount=float(draft.amount), status=draft.status, note=draft.note,
        recommendation_snapshot=json.loads(draft.recommendation_snapshot_json or "{}"),
        evidence_status=draft.evidence_status or "unavailable",
        rag_citations=json.loads(draft.rag_citations_json or "[]"),
        customer_revision_note=draft.customer_revision_note or "",
        revision_requested_at=draft.revision_requested_at, product_name=product_name, advisor_name=advisor_name,
    )
def resolve_profile_uow(request: Request):
    trading_service = getattr(request.app.state, "trading_service", None)
    if trading_service is not None:
        return trading_service.unit_of_work
    from app.repositories.unit_of_work import create_postgres_unit_of_work
    return create_postgres_unit_of_work()


def _risk_operation_response(account, alert):
    if account is not None and account.risk_restriction_status == "restricted":
        status = "restricted"
    elif alert is not None:
        status = "monitoring"
    else:
        status = "normal"
    return RiskOperationStatusResponse(
        status=status,
        restriction_status=account.risk_restriction_status if account else "none",
        restriction_reason=account.risk_restriction_reason if account else "",
        restricted_until=account.risk_restricted_until if account else None,
        latest_alert_id=alert.id if alert else None,
        latest_rule_code=alert.rule_code if alert else None,
        latest_alert_level=alert.alert_level if alert else None,
        latest_alert_status=alert.status if alert else None,
        latest_action_code=alert.action_code if alert else None,
        latest_action_status=alert.action_status if alert else None,
    )


async def _risk_operation_for_user(session, user_id: str):
    account = (await session.execute(select(Account).where(Account.user_id == user_id))).scalar_one_or_none()
    alert = (await session.execute(select(RiskAlert).where(
        RiskAlert.customer_id == user_id,
        RiskAlert.status.in_(["open", "acknowledged", "investigating", "report_pending"]),
    ).order_by(RiskAlert.created_at.desc()).limit(1))).scalars().first()
    return _risk_operation_response(account, alert)

@router.get("/me",response_model=ApiResponse[ProfileSummary])
async def my_summary(request:Request,user:Annotated[User,Depends(current_user)]):
 trading_service = getattr(request.app.state, "trading_service", None)
 if trading_service is None:
  from app.repositories.unit_of_work import create_postgres_unit_of_work
  uow = create_postgres_unit_of_work()
 else:
  uow = trading_service.unit_of_work
 asset_service = AssetSummaryService(
  account_repository=uow.accounts,
  holding_repository=uow.holdings,
  asset_repository=uow.assets,
 )
 async with request.app.state.database.session_factory() as s:
  profile = await uow.profiles.get(s, user.id)
  asset = await asset_service.latest_or_derived(s, user.id)
  holdings = await uow.holdings.list_active(s, user.id)
  details = await HoldingValuationService().details_for(s, holdings)
  holding_items = [HoldingResponse.model_validate(h,from_attributes=True).model_copy(update=details.get(h.id, {})) for h in holdings]
  return ApiResponse(data=ProfileSummary(profile=ProfileResponse.model_validate(profile,from_attributes=True) if profile else None, latest_asset=AssetSnapshotResponse.model_validate(asset,from_attributes=True) if asset else None, holdings=holding_items, risk_operation=await _risk_operation_for_user(s, user.id)))
@router.get("/me/assets/history",response_model=ApiResponse[AssetSnapshotHistoryResponse])
async def asset_history(request: Request, limit: int = 60, user: Annotated[User, Depends(current_user)] = None):
 if limit < 1 or limit > 180:
  raise HTTPException(status_code=422, detail={"code":"INVALID_LIMIT","message":"limit must be between 1 and 180"})
 trading_service = getattr(request.app.state, "trading_service", None)
 if trading_service is None:
  from app.repositories.unit_of_work import create_postgres_unit_of_work
  uow = create_postgres_unit_of_work()
 else:
  uow = trading_service.unit_of_work
 asset_service = AssetSummaryService(
  account_repository=uow.accounts,
  holding_repository=uow.holdings,
  asset_repository=uow.assets,
 )
 async with request.app.state.database.session_factory() as s:
  # Refresh the current derived snapshot before reading history. Holding valuation is recalculated
  # on request, so omitting this step left the trend line behind the current total asset/P&L.
  await asset_service.latest_or_derived(s, user.id)
  await s.commit()
  rows = list((await s.execute(select(CustomerAssetSnapshot).where(CustomerAssetSnapshot.user_id==user.id).order_by(CustomerAssetSnapshot.snapshot_time.desc(), CustomerAssetSnapshot.id.desc()).limit(limit))).scalars().all())
  rows.reverse()
  return ApiResponse(data=AssetSnapshotHistoryResponse(items=[AssetSnapshotResponse.model_validate(row, from_attributes=True) for row in rows], total=len(rows)))

@router.put("/me",response_model=ApiResponse[ProfileResponse])
async def update_profile(request:Request,payload:ProfileRequest,user:Annotated[User,Depends(current_user)]):
 trading_service = getattr(request.app.state, "trading_service", None)
 if trading_service is None:
  from app.repositories.unit_of_work import create_postgres_unit_of_work
  uow = create_postgres_unit_of_work()
 else:
  uow = trading_service.unit_of_work
 async with request.app.state.database.session_factory() as s:
  obj = await uow.profiles.get(s, user.id)
  if obj is None:
   obj = CustomerProfile(id=str(uuid4()), user_id=user.id, **payload.model_dump())
   obj = await uow.profiles.save(s, obj)
  else:
   for k, v in payload.model_dump().items():
    setattr(obj, k, v)
   obj = await uow.profiles.update_values(s, obj)
  await uow.audit_logs.save(s, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="update", resource_type="customer_profile", resource_id=obj.id, detail="customer editable profile updated"))
  await s.commit(); await s.refresh(obj)
  return ApiResponse(data=ProfileResponse.model_validate(obj,from_attributes=True))
@router.post("/me/assets",response_model=ApiResponse[AssetSnapshotResponse],status_code=201)
async def create_asset(request:Request,payload:AssetSnapshotRequest,user:Annotated[User,Depends(current_user)]):
 trading_service = getattr(request.app.state, "trading_service", None)
 if trading_service is None:
  from app.repositories.unit_of_work import create_postgres_unit_of_work
  uow = create_postgres_unit_of_work()
 else:
  uow = trading_service.unit_of_work
 async with request.app.state.database.session_factory() as s:
  obj = CustomerAssetSnapshot(id=str(uuid4()), user_id=user.id, **payload.model_dump())
  obj = await uow.assets.save(s, obj)
  await s.commit(); await s.refresh(obj)
  return ApiResponse(data=AssetSnapshotResponse.model_validate(obj,from_attributes=True))
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
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as s:
        product = await uow.products.save(s, Product(id=str(uuid4()), **payload.model_dump()))
        await s.commit(); await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.put("/staff/products/{product_id}", response_model=ApiResponse[ProductResponse])
async def update_staff_product(request: Request, product_id: str, payload: ProductRequest, user: Annotated[User, Depends(require_permission("product:write"))]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as s:
        product = await uow.products.get_active(s, product_id)
        if product is None: raise HTTPException(status_code=404, detail={"code":"PRODUCT_NOT_FOUND","message":"product not found"})
        for key, value in payload.model_dump().items(): setattr(product, key, value)
        product = await uow.products.update_values(s, product)
        await s.commit(); await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.delete("/staff/products/{product_id}", response_model=ApiResponse[ProductResponse])
async def delete_staff_product(request: Request, product_id: str, user: Annotated[User, Depends(require_permission("product:write"))]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as s:
        product = await uow.products.get_active(s, product_id)
        if product is None: raise HTTPException(status_code=404, detail={"code":"PRODUCT_NOT_FOUND","message":"product not found"})
        product.status="deleted"; product.deleted_at=datetime.now(timezone.utc)
        product = await uow.products.update_values(s, product)
        await uow.audit_logs.save(s, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="soft_delete", resource_type="product", resource_id=product.id, detail=product.name))
        await s.commit(); await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.put("/staff/products/{product_id}/restore", response_model=ApiResponse[ProductResponse])
async def restore_staff_product(request: Request, product_id: str, user: Annotated[User, Depends(require_permission("product:write"))]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as s:
        product = await uow.products.get_by_id(s, product_id)
        if product is None: raise HTTPException(status_code=404, detail={"code":"PRODUCT_NOT_FOUND","message":"product not found"})
        product.status="active"; product.deleted_at=None
        product = await uow.products.update_values(s, product)
        await uow.audit_logs.save(s, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="restore", resource_type="product", resource_id=product.id, detail=product.name))
        await s.commit(); await s.refresh(product)
        return ApiResponse(data=ProductResponse.model_validate(product, from_attributes=True))

@router.get("/products",response_model=ApiResponse[list[ProductResponse]])
async def products(request:Request):
 uow = resolve_profile_uow(request)
 async with request.app.state.database.session_factory() as s:
  rows = await uow.products.list_active(s)
  return ApiResponse(data=[ProductResponse.model_validate(x,from_attributes=True) for x in rows])
async def _active_advisor_authorization(session, customer_id: str, advisor_id: str, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    return (await session.execute(select(CustomerAdvisorAuthorization).where(
        CustomerAdvisorAuthorization.customer_id == customer_id,
        CustomerAdvisorAuthorization.advisor_id == advisor_id,
        CustomerAdvisorAuthorization.status == "active",
        CustomerAdvisorAuthorization.effective_from <= now,
        or_(CustomerAdvisorAuthorization.effective_to.is_(None), CustomerAdvisorAuthorization.effective_to >= now),
    ).order_by(CustomerAdvisorAuthorization.created_at.desc()))).scalars().first()


def _customer_summary_item(row, customer_tier_value: str, tier_reasons: list[str], risk_operation: RiskOperationStatusResponse | None = None) -> StaffCustomerListItem:
    customer = row.user
    profile = row.profile
    asset = row.asset
    risk = row.risk
    return StaffCustomerListItem(
        id=customer.id, username=customer.username, display_name=customer.display_name, status=customer.status,
        profile=ProfileResponse.model_validate(profile, from_attributes=True) if profile else None,
        latest_asset=AssetSnapshotResponse.model_validate(asset, from_attributes=True) if asset else None,
        customer_tier=customer_tier_value, tier_reasons=tier_reasons,
        risk_level=risk.risk_level if risk else None, risk_score=risk.score if risk else None,
        risk_status=risk.status if risk else None, holding_count=row.holding_count,
        risk_operation_status=risk_operation.status if risk_operation else "normal",
        risk_restriction_status=risk_operation.restriction_status if risk_operation else "none",
        risk_restriction_reason=risk_operation.restriction_reason if risk_operation else "",
        latest_risk_rule_code=risk_operation.latest_rule_code if risk_operation else None,
        latest_risk_alert_level=risk_operation.latest_alert_level if risk_operation else None,
    )


async def _list_customer_summaries(request: Request, q: str, tier: str, customer_type: str, limit: int, offset: int, allowed_customer_ids: set[str] | None = None) -> StaffCustomerListResponse:
    limit = min(max(limit, 1), 100)
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as s:
        rows = await uow.customer_queries.list_staff_customer_views(s, q=q, limit=10000, offset=0)
        filtered = []
        for row in rows:
            if allowed_customer_ids is not None and row.user.id not in allowed_customer_ids:
                continue
            profile = row.profile
            asset = row.asset
            customer_type_value = profile.customer_type if profile else "individual"
            investable_asset = asset.investable_asset if asset else 0
            customer_tier_value, tier_reasons = calculate_customer_tier(customer_type_value, investable_asset)
            if tier.strip() and customer_tier_value != tier.strip():
                continue
            if customer_type.strip() and customer_type_value != customer_type.strip():
                continue
            filtered.append((row, customer_tier_value, tier_reasons))
        page = filtered[max(offset, 0):max(offset, 0) + limit]
        page_user_ids = [row.user.id for row, _, _ in page]
        accounts = {account.user_id: account for account in (await s.execute(select(Account).where(Account.user_id.in_(page_user_ids)))).scalars().all()} if page_user_ids else {}
        alerts = {}
        if page_user_ids:
            for alert in (await s.execute(select(RiskAlert).where(
                RiskAlert.customer_id.in_(page_user_ids),
                RiskAlert.status.in_(["open", "acknowledged", "investigating", "report_pending"]),
            ).order_by(RiskAlert.created_at.desc()))).scalars().all():
                alerts.setdefault(alert.customer_id, alert)
        return StaffCustomerListResponse(
            items=[_customer_summary_item(row, customer_tier_value, tier_reasons, _risk_operation_response(accounts.get(row.user.id), alerts.get(row.user.id))) for row, customer_tier_value, tier_reasons in page],
            total=len(filtered),
        )


@router.get("/staff/customers", response_model=ApiResponse[StaffCustomerListResponse])
async def staff_customers(request: Request, user: Annotated[User, Depends(require_staff_permission("customer:read_all"))], q: str = "", tier: str = "", customer_type: str = "", limit: int = 50, offset: int = 0):
    """Operational customer-management list: all active customers within customer:read_all scope."""
    return ApiResponse(data=await _list_customer_summaries(request, q, tier, customer_type, limit, offset))


@router.get("/staff/advisor-customers", response_model=ApiResponse[StaffCustomerListResponse])
async def staff_advisor_customers(request: Request, user: Annotated[User, Depends(advisor_user)], q: str = "", tier: str = "", customer_type: str = "", limit: int = 50, offset: int = 0):
    """Advisor workbench list: only customers with an active, effective authorization for the current advisor."""
    now = datetime.now(timezone.utc)
    async with request.app.state.database.session_factory() as s:
        authorized_customer_ids = set((await s.execute(select(CustomerAdvisorAuthorization.customer_id).where(
            CustomerAdvisorAuthorization.advisor_id == user.id,
            CustomerAdvisorAuthorization.status == "active",
            CustomerAdvisorAuthorization.effective_from <= now,
            or_(CustomerAdvisorAuthorization.effective_to.is_(None), CustomerAdvisorAuthorization.effective_to >= now),
        ))).scalars().all())
    if not authorized_customer_ids:
        return ApiResponse(data=StaffCustomerListResponse(items=[], total=0))
    return ApiResponse(data=await _list_customer_summaries(request, q, tier, customer_type, limit, offset, authorized_customer_ids))



@router.get("/staff/customers/{user_id}", response_model=ApiResponse[StaffCustomerDetail])
async def managed_customer_summary(request: Request, user_id: str, user: Annotated[User, Depends(require_staff_permission("customer:read_all"))]):
    uow = resolve_profile_uow(request)
    asset_service = AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets)
    async with request.app.state.database.session_factory() as s:
        customer = await uow.users.get(s, user_id)
        if customer is None or not await uow.users.is_customer(s, user_id):
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
        profile = await uow.profiles.get(s, user_id)
        asset = await asset_service.latest_or_derived(s, user_id)
        holdings = await uow.holdings.list_active(s, user_id)
        details = await HoldingValuationService().details_for(s, holdings)
        holding_items = [HoldingResponse.model_validate(h, from_attributes=True).model_copy(update=details.get(h.id, {})) for h in holdings]
        risk_operation = await _risk_operation_for_user(s, user_id)
        latest_risk_rule_code = risk_operation.latest_rule_code
        latest_risk_alert_level = risk_operation.latest_alert_level
        return ApiResponse(data=StaffCustomerDetail(
            user=StaffCustomerListItem(
                id=str(customer.id), username=customer.username, display_name=customer.display_name,
                status=customer.status,
                profile=ProfileResponse.model_validate(profile, from_attributes=True) if profile else None,
                latest_asset=AssetSnapshotResponse.model_validate(asset, from_attributes=True) if asset else None,
                customer_tier=(profile.customer_tier if profile else "ordinary"),
                risk_level=None,
                risk_operation_status=risk_operation.status,
                risk_restriction_status=risk_operation.restriction_status,
                risk_restriction_reason=risk_operation.restriction_reason,
                latest_risk_rule_code=latest_risk_rule_code,
                latest_risk_alert_level=latest_risk_alert_level,
            ),
            profile=ProfileResponse.model_validate(profile, from_attributes=True) if profile else None,
            latest_asset=AssetSnapshotResponse.model_validate(asset, from_attributes=True) if asset else None,
            holdings=holding_items,
            risk_operation=risk_operation,
        ))


@router.get("/staff/customer/{user_id}", response_model=ApiResponse[ProfileSummary])
async def staff_summary(request: Request, user_id: str, user: Annotated[User, Depends(advisor_user)]):
    uow = resolve_profile_uow(request)
    asset_service = AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets)
    async with request.app.state.database.session_factory() as s:
        if await _active_advisor_authorization(s, user_id, str(user.id)) is None:
            raise HTTPException(status_code=403, detail={"code": "ADVISOR_AUTHORIZATION_REQUIRED", "message": "active advisor authorization required"})
        profile = await uow.profiles.get(s, user_id)
        if profile is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
        asset = await asset_service.latest_or_derived(s, user_id)
        holdings = await uow.holdings.list_active(s, user_id)
        details = await HoldingValuationService().details_for(s, holdings)
        holding_items = [HoldingResponse.model_validate(h, from_attributes=True).model_copy(update=details.get(h.id, {})) for h in holdings]
        return ApiResponse(data=ProfileSummary(
            profile=ProfileResponse.model_validate(profile, from_attributes=True),
            latest_asset=AssetSnapshotResponse.model_validate(asset, from_attributes=True) if asset else None,
            holdings=holding_items,
            risk_operation=await _risk_operation_for_user(s, user_id),
        ))


@router.post("/me/advisor-authorizations", response_model=ApiResponse[AdvisorAuthorizationResponse], status_code=201)
async def create_advisor_authorization(request: Request, payload: AdvisorAuthorizationRequest, user: Annotated[User, Depends(current_user)]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as session:
        advisor = await uow.users.get(session, payload.advisor_id)
        if advisor is None:
            raise HTTPException(status_code=404, detail={"code": "ADVISOR_NOT_FOUND", "message": "advisor not found"})
        mutation = await AdvisorAuthorizationService.grant_or_update(
            session, customer_id=user.id, advisor_id=payload.advisor_id, values=payload.model_dump(exclude={"advisor_id"})
        )
        authorization = mutation.authorization
        await session.flush()
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="grant", resource_type="advisor_authorization", resource_id=authorization.id, detail=payload.model_dump_json()))
        AdvisorAuthorizationService.enqueue_scope_invalidation_events(
            session, advisor_ids=[authorization.advisor_id], mutation_source="customer_self_service"
        )
        await session.commit()
        await session.refresh(authorization)
        return ApiResponse(data=AdvisorAuthorizationResponse.model_validate(authorization, from_attributes=True))

@router.get("/me/advisor-authorizations", response_model=ApiResponse[dict])
async def list_advisor_authorizations(request: Request, limit: int = 20, offset: int = 0, user: Annotated[User, Depends(current_user)] = None):
    async with request.app.state.database.session_factory() as session:
        query=select(CustomerAdvisorAuthorization).where(CustomerAdvisorAuthorization.customer_id == user.id, CustomerAdvisorAuthorization.status == "active").order_by(CustomerAdvisorAuthorization.created_at.desc())
        total=int((await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
        rows=list((await session.execute(query.offset(max(offset,0)).limit(min(max(limit,1),100)))).scalars().all())
        advisor_ids={row.advisor_id for row in rows}
        advisors={item.id:item for item in (await session.execute(select(User).where(User.id.in_(advisor_ids)))).scalars().all()} if advisor_ids else {}
        items=[]
        for row in rows:
            payload={**row.__dict__, "advisor_name": advisors.get(row.advisor_id).display_name if advisors.get(row.advisor_id) else ""}
            items.append(AdvisorAuthorizationResponse.model_validate(payload))
        return ApiResponse(data={"items":items,"total":total})

@router.delete("/me/advisor-authorizations/{authorization_id}", response_model=ApiResponse[dict])
async def revoke_advisor_authorization(request: Request, authorization_id: str, user: Annotated[User, Depends(current_user)]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as session:
        row = await AdvisorAuthorizationService.revoke_for_customer(session, authorization_id=authorization_id, customer_id=user.id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "AUTHORIZATION_NOT_FOUND", "message": "advisor authorization not found"})
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="revoke", resource_type="advisor_authorization", resource_id=row.id, detail="customer revoked authorization"))
        AdvisorAuthorizationService.enqueue_scope_invalidation_events(
            session, advisor_ids=[row.advisor_id], mutation_source="customer_self_service"
        )
        await session.commit()
        return ApiResponse(data={"id": row.id, "status": row.status})

@router.get("/staff/advisors", response_model=ApiResponse[dict])
async def staff_advisors(request: Request, limit: int = 20, offset: int = 0, user: Annotated[User, Depends(current_user)] = None):
    async with request.app.state.database.session_factory() as session:
        query=select(User).where(User.status == "active", User.is_super_admin.is_(True) | User.roles.any(code="advisor"))
        total=int((await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
        rows=list((await session.execute(query.offset(max(offset,0)).limit(min(max(limit,1),100)))).scalars().all())
        return ApiResponse(data={"items":[{"id": row.id, "username": row.username, "display_name": row.display_name} for row in rows],"total":total})

@router.get("/staff/customer/{user_id}/memory-facts", response_model=ApiResponse[dict])
async def staff_customer_memory_facts(request: Request, user_id: str, user: Annotated[User, Depends(current_user)]):
    """员工/投顾按授权读取某客户的低风险记忆事实（只读）。

    只暴露低风险、非敏感、可归因的偏好事实（risk_preference/product_preference/
    investment_horizon_preference/active_product）与最近会话摘要，供员工服务客户时作上下文。
    不绕过任何权限/风控/确认门禁；不写客户记忆。
    授权：super_admin 可读任意；否则需 customer:read_all 或对该客户存在 active 投顾授权。
    """
    from app.services.shared_memory_service import SharedMemoryService
    whitelist = {"risk_preference", "product_preference", "investment_horizon_preference", "active_product"}
    async with request.app.state.database.session_factory() as session:
        target = (await session.execute(select(User).where(User.id == user_id, User.status == "active"))).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
        if not user.is_super_admin:
            perms = {p.code for role in user.roles for p in role.permissions}
            has_read_all = "customer:read_all" in perms
            has_authz = (await _active_advisor_authorization(session, user_id, str(user.id))) is not None
            if not has_read_all and not has_authz:
                raise HTTPException(status_code=403, detail={"code": "CUSTOMER_MEMORY_FORBIDDEN", "message": "无权读取该客户记忆"})
    memory = CustomerMemoryService(request.app.state.database, None)
    rows = await memory.load_long_term(customer_id=user_id)
    facts = {r.key: str(r.value) for r in rows if getattr(r, "key", None) in whitelist and getattr(r, "value", "")}
    try:
        shared = await SharedMemoryService(request.app.state.database, None).recall(customer_id=user_id, agent_type="advisor", memory_type="observation")
        for r in shared:
            if getattr(r, "key", None) in whitelist and getattr(r, "value", "") and r.key not in facts:
                facts[r.key] = str(r.value)
    except Exception:  # noqa: BLE001 - 记忆异常不阻塞档案读取
        pass
    summary = ""
    try:
        rec = await SharedMemoryService(request.app.state.database, None).recall(customer_id=user_id, agent_type="customer_service", key="conversation_summary")
        if rec:
            summary = str(rec[0].value or "")
    except Exception:  # noqa: BLE001 - 记忆异常不阻塞档案读取
        pass
    return ApiResponse(data={"customer_id": user_id, "display_name": target.display_name, "username": target.username, "facts": facts, "conversation_summary": summary})


@router.post("/staff/supervisor/advisor-ask", response_model=ApiResponse[dict])
async def staff_supervisor_advisor_ask(request: Request, payload: AdvisorAskRequest, user: Annotated[User, Depends(current_user)]):
    """员工侧投顾智能回复：把被服务客户的共享记忆 + 风险 + 资产作为上下文，调 Qwen 生成只读说明。

    只读、不触发任何交易、不修改权限/授权；越权与合规由授权校验 + 系统提示双重约束。
    授权同 memory-facts：super_admin 任意；否则需 customer:read_all 或对该客户 active 投顾授权。
    """
    from app.core.settings import get_settings
    from app.infrastructure.qwen import QwenProvider
    from app.services.shared_memory_service import SharedMemoryService
    whitelist = {"risk_preference", "product_preference", "investment_horizon_preference", "active_product"}
    customer_id = payload.customer_id
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail={"code": "ADVISOR_ASK_EMPTY", "message": "query is required"})
    uow = resolve_profile_uow(request)
    display_name = customer_id
    risk_level = ""
    total_asset = 0.0
    cash_balance = 0.0
    async with request.app.state.database.session_factory() as session:
        target = (await session.execute(select(User).where(User.id == customer_id, User.status == "active"))).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
        display_name = target.display_name or customer_id
        if not user.is_super_admin:
            perms = {p.code for role in user.roles for p in role.permissions}
            has_read_all = "customer:read_all" in perms
            has_authz = (await _active_advisor_authorization(session, customer_id, str(user.id))) is not None
            if not has_read_all and not has_authz:
                raise HTTPException(status_code=403, detail={"code": "ADVISOR_ASK_FORBIDDEN", "message": "无权为该客户提供投顾分析"})
        try:
            active_risk = await uow.risks.get_active(session, customer_id) if hasattr(uow, "risks") else None
            risk_level = active_risk.risk_level if active_risk else ""
        except Exception:  # noqa: BLE001
            risk_level = ""
        try:
            asset_service = AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets)
            asset = await asset_service.latest_or_derived(session, customer_id)
            if asset is not None:
                total_asset = float(getattr(asset, "total_asset", 0) or 0)
                cash_balance = float(getattr(asset, "cash_balance", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
    memory = CustomerMemoryService(request.app.state.database, None)
    rows = await memory.load_long_term(customer_id=customer_id)
    facts = {r.key: str(r.value) for r in rows if getattr(r, "key", None) in whitelist and getattr(r, "value", "")}
    try:
        shared = await SharedMemoryService(request.app.state.database, None).recall(customer_id=customer_id, agent_type="advisor", memory_type="observation")
        for r in shared:
            if getattr(r, "key", None) in whitelist and getattr(r, "value", "") and r.key not in facts:
                facts[r.key] = str(r.value)
    except Exception:  # noqa: BLE001
        pass
    summary = ""
    try:
        rec = await SharedMemoryService(request.app.state.database, None).recall(customer_id=customer_id, agent_type="customer_service", key="conversation_summary")
        if rec:
            summary = str(rec[0].value or "")
    except Exception:  # noqa: BLE001
        pass
    lines = [f"客户：{display_name}"]
    if risk_level:
        lines.append(f"风险测评等级：{risk_level}")
    if total_asset or cash_balance:
        lines.append(f"总资产：¥{total_asset:,.0f}；现金余额：¥{cash_balance:,.0f}")
    if facts:
        lines.append("客户已知偏好（可能来自客户自述/记忆）：" + "；".join(f"{k}={facts[k]}" for k in facts))
    if summary:
        lines.append("最近会话摘要：" + summary[:800])
    system = (
        "你是财富管理平台的资深投顾助手，服务于内部员工（投顾/管理员），输出是供员工参考的内部建议。"
        "请仅依据下面提供的客户事实作答，保守、合规、只读。"
        "禁止援引具体法规/办法的条款号，也不要以“根据《XX办法》”等口吻编造出处；不要假装引用公司制度原文。"
        "不得声称已执行交易、不得替客户下单或修改授权、不得编造客户未提供的数字、风险等级或偏好。"
        "若客户偏好/投资目标/风险信息不足，明确列出缺少什么，并说明据此只能给出方向性参考而非完整配置结论。"
        "结尾附一句风险提示（不构成投资建议）。"
    )
    provider = QwenProvider(get_settings())
    try:
        answer = await provider.complete(
            system=system,
            user="客户上下文：\n" + "\n".join(lines) + "\n\n员工问题：" + query,
        )
    finally:
        await provider.close()
    return ApiResponse(data={"customer_id": customer_id, "query": query, "answer": answer, "context": {"risk_level": risk_level, "facts": facts, "conversation_summary": summary}})


async def _advisor_ask_context(request: Request, payload: AdvisorAskRequest, user: User) -> dict:
    """投顾智能回复：授权校验 + 汇总客户共享记忆/风险/资产为上下文。返回 system/user prompt。"""
    from app.services.shared_memory_service import SharedMemoryService
    whitelist = {"risk_preference", "product_preference", "investment_horizon_preference", "active_product"}
    customer_id = payload.customer_id
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail={"code": "ADVISOR_ASK_EMPTY", "message": "query is required"})
    uow = resolve_profile_uow(request)
    display_name = customer_id
    risk_level = ""
    total_asset = 0.0
    cash_balance = 0.0
    async with request.app.state.database.session_factory() as session:
        target = (await session.execute(select(User).where(User.id == customer_id, User.status == "active"))).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
        display_name = target.display_name or customer_id
        if not user.is_super_admin:
            perms = {p.code for role in user.roles for p in role.permissions}
            has_read_all = "customer:read_all" in perms
            has_authz = (await _active_advisor_authorization(session, customer_id, str(user.id))) is not None
            if not has_read_all and not has_authz:
                raise HTTPException(status_code=403, detail={"code": "ADVISOR_ASK_FORBIDDEN", "message": "无权为该客户提供投顾分析"})
        try:
            active_risk = await uow.risks.get_active(session, customer_id) if hasattr(uow, "risks") else None
            risk_level = active_risk.risk_level if active_risk else ""
        except Exception:  # noqa: BLE001
            risk_level = ""
        try:
            asset_service = AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets)
            asset = await asset_service.latest_or_derived(session, customer_id)
            if asset is not None:
                total_asset = float(getattr(asset, "total_asset", 0) or 0)
                cash_balance = float(getattr(asset, "cash_balance", 0) or 0)
        except Exception:  # noqa: BLE001
            pass
    memory = CustomerMemoryService(request.app.state.database, None)
    rows = await memory.load_long_term(customer_id=customer_id)
    facts = {r.key: str(r.value) for r in rows if getattr(r, "key", None) in whitelist and getattr(r, "value", "")}
    try:
        shared = await SharedMemoryService(request.app.state.database, None).recall(customer_id=customer_id, agent_type="advisor", memory_type="observation")
        for r in shared:
            if getattr(r, "key", None) in whitelist and getattr(r, "value", "") and r.key not in facts:
                facts[r.key] = str(r.value)
    except Exception:  # noqa: BLE001
        pass
    summary = ""
    try:
        rec = await SharedMemoryService(request.app.state.database, None).recall(customer_id=customer_id, agent_type="customer_service", key="conversation_summary")
        if rec:
            summary = str(rec[0].value or "")
    except Exception:  # noqa: BLE001
        pass
    lines = [f"客户：{display_name}"]
    if risk_level:
        lines.append(f"风险测评等级：{risk_level}")
    if total_asset or cash_balance:
        lines.append(f"总资产：¥{total_asset:,.0f}；现金余额：¥{cash_balance:,.0f}")
    if facts:
        lines.append("客户已知偏好（可能来自客户自述/记忆）：" + "；".join(f"{k}={facts[k]}" for k in facts))
    if summary:
        lines.append("最近会话摘要：" + summary[:800])
    system = (
        "你是财富管理平台的资深投顾助手，服务于内部员工（投顾/管理员），输出是供员工参考的内部建议。"
        "请仅依据下面提供的客户事实和真实业务作答，保守、合规、只读。"
        "若员工问题涉及“某产品库里是什么样/值不值/适不适合这位客户”这类，你应先调用库内只读工具"
        "（find_products 查真实在售产品与其 risk_level/起购/期限；product_suitability 查对该客户的真实适当性命中），"
        "再基于工具返回的库内真实档位与命中结论作答。不得凭空对具体产品设定档位、收益率区间或适配结论。"
        "禁止援引具体法规/办法的条款号，也不要以“根据《XX办法》”等口吻编造出处；不要假装引用公司制度原文。"
        "不得声称已执行交易、不得替客户下单或修改授权、不得编造客户未提供的数字、风险等级或偏好。"
        "若客户偏好/投资目标/风险信息不足，明确列出缺少什么，并说明据此只能给出方向性参考而非完整配置结论。"
        "结尾附一句风险提示（不构成投资建议）。"
    )
    return {"customer_id": customer_id, "query": query, "system": system, "user": "客户上下文：\n" + "\n".join(lines) + "\n\n员工问题：" + query}


@router.post("/staff/supervisor/advisor-ask/stream")
async def staff_supervisor_advisor_ask_stream(request: Request, payload: AdvisorAskRequest, user: Annotated[User, Depends(current_user)]):
    """员工侧投顾智能回复（SSE 真流式 + 有界只读工具调用）。

    结构化结果不走这里；模型可选择调用 find_products / product_suitability 获取库内真实
    产品与适当性命中后，再以流式把回答吐出来。工具轮非流式，仅最终文本流式。
    """
    from app.core.settings import get_settings
    from app.common.logging.context import get_log_context
    from app.infrastructure.qwen import QwenProvider
    from app.services.advisor_ask_agent import AdvisorBoundedLoop
    from app.services.advisor_tools import AdvisorReadTools
    ctx = await _advisor_ask_context(request, payload, user)
    provider = QwenProvider(get_settings())
    conversation_id = payload.query
    try:
        trace_id = (get_log_context() or {}).get("trace_id") or str(uuid4())
    except Exception:  # noqa: BLE001
        trace_id = str(uuid4())
    uow_tools = resolve_profile_uow(request)
    advisor_tools = AdvisorReadTools(uow_tools)
    # 确定性单次接地：题面能召回真实产品就执行 find_products+product_suitability 一次，
    # 纯画像/开放句（无产品命中）不进工具、直接流式，首字更快。
    loop = AdvisorBoundedLoop(provider, advisor_tools, request.app.state.database)
    final_user, _had_tool = await loop.run_to_text_user(
        conversation_id=str(trace_id), actor_user_id=str(user.id), customer_id=payload.customer_id,
        query=payload.query.strip(), context_user=ctx["user"], system=ctx["system"],
    )

    async def _gen():
        try:
            async for delta in provider.stream_text(system=ctx["system"], user=final_user):
                yield f"data: {json.dumps({'t': delta}, ensure_ascii=False)}\n\n"
            yield "data: {\"done\":true}\n\n"
        except Exception as exc:  # noqa: BLE001 - 流式异常转 SSE 错误事件，前端据此兜底
            yield f"data: {json.dumps({'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False)}\n\n"
        finally:
            await provider.close()

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/staff/recommendations", response_model=ApiResponse[StaffRecommendationResponse])
async def staff_recommendations(request: Request, payload: StaffRecommendationRequest, user: Annotated[User, Depends(advisor_user)]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as session:
        if await _active_advisor_authorization(session, payload.customer_id, str(user.id)) is None:
            raise HTTPException(status_code=403, detail={"code": "ADVISOR_AUTHORIZATION_REQUIRED", "message": "active advisor authorization required"})
        profile = await uow.profiles.get(session, payload.customer_id)
        if profile is None:
            raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
        service = SuitabilityService(risk_repository=uow.risks, product_repository=uow.products, profile_repository=uow.profiles, asset_service=AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets))
        recommendation = await service.recommend(session, payload.customer_id, risk_operation=await _risk_operation_for_user(session, payload.customer_id))
        data = StaffRecommendationResponse(
            user_id=recommendation.user_id,
            context=recommendation.context,
            matches=recommendation.matches,
            excluded=recommendation.excluded,
            warnings=recommendation.warnings,
            generated_at=recommendation.generated_at,
            generated_for=payload.customer_id,
        )
        return ApiResponse(data=data)

async def _create_advisor_operation_draft(session, uow, *, customer_id: str, advisor_id: str, product_id: str, amount: float, note: str = "") -> AdvisorOperationDraft:
    """投顾代客申购草稿的受控创建（结构化产品/金额入口）。

    顺序执行：客户档案存在 → 产品在售 → 投顾对该客户存在有效授权 →
    授权动作含 buy → 产品在授权范围 → 单笔限额 → 当日累计限额 →
    产品风险 ≤ 授权风险上限 → 适当性命中。全部通过才落 AdvisorOperationDraft
    （status=pending_customer_confirmation，绝不直接成交，等客户确认后另行 submit-order）。
    被调用方负责 commit。
    """
    profile = await uow.profiles.get(session, customer_id)
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "CUSTOMER_NOT_FOUND", "message": "customer not found"})
    product = await uow.products.get_active(session, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail={"code": "PRODUCT_NOT_FOUND", "message": "product not found"})
    now = datetime.now(timezone.utc)
    authorization = await _active_advisor_authorization(session, customer_id, advisor_id, now)
    if authorization is None:
        raise HTTPException(status_code=403, detail={"code": "ADVISOR_AUTHORIZATION_REQUIRED", "message": "active advisor authorization required"})
    allowed_actions = {item.strip() for item in authorization.allowed_actions.split(",") if item.strip()}
    if "buy" not in allowed_actions:
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_ACTION_NOT_ALLOWED", "message": "buy action is not authorized"})
    if authorization.product_scope != "*" and product_id not in {item.strip() for item in authorization.product_scope.split(",") if item.strip()}:
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_PRODUCT_NOT_ALLOWED", "message": "product is outside authorization scope"})
    if amount > authorization.max_single_amount:
        raise HTTPException(status_code=409, detail={"code": "AUTHORIZATION_SINGLE_LIMIT_EXCEEDED", "message": "single operation amount exceeds authorization limit"})
    daily_total = (await session.execute(select(func.coalesce(func.sum(AdvisorOperationDraft.amount), 0)).where(
        AdvisorOperationDraft.customer_id == customer_id,
        AdvisorOperationDraft.advisor_id == advisor_id,
        AdvisorOperationDraft.status.in_(["pending_customer_confirmation", "customer_confirmed"]),
        AdvisorOperationDraft.created_at >= func.date_trunc("day", now),
    ))).scalar_one()
    if float(daily_total) + amount > authorization.max_daily_amount:
        raise HTTPException(status_code=409, detail={"code": "AUTHORIZATION_DAILY_LIMIT_EXCEEDED", "message": "daily operation amount exceeds authorization limit"})
    risk_order = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}
    if risk_order.get(product.risk_level, 99) > risk_order.get(authorization.risk_level_ceiling, 0):
        raise HTTPException(status_code=403, detail={"code": "AUTHORIZATION_RISK_LIMIT_EXCEEDED", "message": "product risk exceeds authorization ceiling"})
    suitability = await SuitabilityService(
        risk_repository=uow.risks, product_repository=uow.products, profile_repository=uow.profiles,
        asset_service=AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets),
    ).recommend(session, customer_id)
    match = next((item for item in suitability.matches if item.product_id == product_id), None)
    if match is None:
        raise HTTPException(status_code=409, detail={"code": "ADVISOR_DRAFT_SUITABILITY_FAILED", "message": "selected product is not currently suitable for customer"})
    asset = await AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets).latest_or_derived(session, customer_id)
    active_risk = await uow.risks.get_active(session, customer_id) if hasattr(uow, "risks") else None
    snapshot = {
        "generated_at": now.isoformat(), "customer_id": customer_id,
        "customer_profile": {"risk_level": active_risk.risk_level if active_risk else "C1", "customer_tier": profile.customer_tier},
        "asset": {"total_asset": str(asset.total_asset) if asset else "0", "investable_asset": str(asset.investable_asset) if asset else "0"},
        "product": {"id": product.id, "name": product.name, "risk_level": product.risk_level, "minimum_amount": str(product.minimum_amount), "status": product.status},
        "authorization": {"id": authorization.id, "effective_to": authorization.effective_to.isoformat() if authorization.effective_to else None, "risk_level_ceiling": authorization.risk_level_ceiling, "max_single_amount": str(authorization.max_single_amount)},
        "suitability": {"matched": True, "reasons": match.reasons},
    }
    draft = AdvisorOperationDraft(id=str(uuid4()), customer_id=customer_id, advisor_id=advisor_id, product_id=product_id, side="buy", amount=amount, note=note, status="pending_customer_confirmation", recommendation_snapshot_json=json.dumps(snapshot, ensure_ascii=False), evidence_status="unavailable", rag_citations_json="[]")
    session.add(draft)
    return draft


@router.post("/staff/operation-drafts", response_model=ApiResponse[AdvisorDraftResponse], status_code=201)
async def create_staff_operation_draft(request: Request, payload: AdvisorDraftRequest, user: Annotated[User, Depends(advisor_user)]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as session:
        draft = await _create_advisor_operation_draft(session, uow, customer_id=payload.customer_id, advisor_id=str(user.id), product_id=payload.product_id, amount=payload.amount, note=payload.note)
        await session.commit(); await session.refresh(draft)
        return ApiResponse(data=_advisor_draft_response(draft))


@router.post("/staff/operation-drafts/natural-language", response_model=ApiResponse[AdvisorNaturalLanguageDraftResponse])
async def create_staff_operation_draft_natural_language(request: Request, payload: AdvisorNaturalLanguageDraftRequest, user: Annotated[User, Depends(advisor_user)]):
    """投顾代客下单（自然语言）。

    客户必须是当前投顾已授权的目标客户（authorized scope）；授权校验在受控创建内部完成，
    只解析‘申购哪个产品、金额多少’，随后走与结构化入口完全相同的草稿+授权+适当性+风控门禁。
    返回 awaiting_customer_confirmation（含草稿）或 needs_clarification（含候选产品）/precheck_failed。
    """
    uow = resolve_profile_uow(request)
    advisor_id = str(user.id)
    parsed = NaturalLanguageOrderDraftService.parse_purchase_intent(payload.query)
    missing: list[str] = []
    if not parsed.product_name:
        missing.append("product_name")
    if parsed.amount is None:
        missing.append("amount")

    if missing:
        options: list[AdvisorNaturalLanguageProductOption] = []
        if parsed.product_name and "product_name" in missing:
            async with request.app.state.database.session_factory() as session:
                products = await uow.products.list_active(session)
            needle = parsed.product_name.replace(" ", "")
            partial = [p for p in products if needle in p.name.replace(" ", "") or p.name.replace(" ", "") in needle]
            options = [AdvisorNaturalLanguageProductOption(id=p.id, name=p.name, risk_level=p.risk_level, minimum_amount=float(p.minimum_amount)) for p in partial[:8]]
        return ApiResponse(data=AdvisorNaturalLanguageDraftResponse(
            status="needs_clarification", customer_id=payload.customer_id, advisor_id=advisor_id,
            clarification="请补充" + ("产品名称" if "product_name" in missing else "") + ("和" if len(missing) == 2 else "") + ("申购金额" if "amount" in missing else "") + "，例如：代客户申购“稳健增值计划”10万元。",
            missing_fields=missing, amount=float(parsed.amount) if parsed.amount is not None else None,
            product_options=options,
        ))

    async with request.app.state.database.session_factory() as session:
        products = await uow.products.list_active(session)
    needle = parsed.product_name.replace(" ", "")
    matches = [p for p in products if needle == p.name.replace(" ", "")]
    if not matches:
        partial = [p for p in products if needle in p.name.replace(" ", "") or p.name.replace(" ", "") in needle]
        options = [AdvisorNaturalLanguageProductOption(id=p.id, name=p.name, risk_level=p.risk_level, minimum_amount=float(p.minimum_amount)) for p in partial[:8]]
        return ApiResponse(data=AdvisorNaturalLanguageDraftResponse(
            status="needs_clarification", customer_id=payload.customer_id, advisor_id=advisor_id,
            clarification="未找到唯一匹配的在售产品，请从候选产品中选择或补充完整产品名称。",
            missing_fields=["product_name"], amount=float(parsed.amount) if parsed.amount is not None else None, product_options=options,
        ))
    if len(matches) > 1:
        options = [AdvisorNaturalLanguageProductOption(id=p.id, name=p.name, risk_level=p.risk_level, minimum_amount=float(p.minimum_amount)) for p in matches[:8]]
        return ApiResponse(data=AdvisorNaturalLanguageDraftResponse(
            status="needs_clarification", customer_id=payload.customer_id, advisor_id=advisor_id,
            clarification="匹配到多个在售产品，请选择具体产品。", missing_fields=["product_name"],
            amount=float(parsed.amount) if parsed.amount is not None else None, product_options=options,
        ))

    product = matches[0]
    async with request.app.state.database.session_factory() as session:
        try:
            draft = await _create_advisor_operation_draft(session, uow, customer_id=payload.customer_id, advisor_id=advisor_id, product_id=product.id, amount=float(parsed.amount), note=f"投顾自然语言代客下单：{payload.query}")
            await session.commit(); await session.refresh(draft)
            return ApiResponse(data=AdvisorNaturalLanguageDraftResponse(
                status="awaiting_customer_confirmation", customer_id=payload.customer_id, advisor_id=advisor_id,
                clarification=f"已为授权客户生成代客申购草稿，等待客户确认。产品：{product.name}；金额：{float(parsed.amount):,.2f} 元。",
                amount=float(parsed.amount), draft=_advisor_draft_response(draft, product_name=product.name),
            ))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"code": "DRAFT_FAILED", "message": str(exc.detail)}
            return ApiResponse(data=AdvisorNaturalLanguageDraftResponse(
                status="precheck_failed", customer_id=payload.customer_id, advisor_id=advisor_id,
                clarification=f"{detail.get('message', '受控校验未通过。')}（{detail.get('code', '')}）",
                amount=float(parsed.amount) if parsed.amount is not None else None,
                precheck_reasons=[str(detail.get('message', detail))],
            ))

@router.get("/me/operation-drafts", response_model=ApiResponse[dict])
async def list_my_operation_drafts(request: Request, limit: int = 20, offset: int = 0, user: Annotated[User, Depends(current_user)] = None):
    async with request.app.state.database.session_factory() as session:
        query=select(AdvisorOperationDraft).where(AdvisorOperationDraft.customer_id == user.id).order_by(AdvisorOperationDraft.created_at.desc())
        total=int((await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
        rows=list((await session.execute(query.offset(max(offset,0)).limit(min(max(limit,1),100)))).scalars().all())
        product_ids={row.product_id for row in rows}; advisor_ids={row.advisor_id for row in rows}; products={x.id:x for x in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()} if product_ids else {}; advisors={x.id:x for x in (await session.execute(select(User).where(User.id.in_(advisor_ids)))).scalars().all()} if advisor_ids else {}
        return ApiResponse(data={"items":[_advisor_draft_response(row, products.get(row.product_id).name if products.get(row.product_id) else "", advisors.get(row.advisor_id).display_name if advisors.get(row.advisor_id) else "") for row in rows],"total":total})

@router.post("/me/operation-drafts/{draft_id}/reject", response_model=ApiResponse[AdvisorDraftResponse])
async def reject_my_operation_draft(request: Request, draft_id: str, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as session:
        draft = (await session.execute(select(AdvisorOperationDraft).where(AdvisorOperationDraft.id == draft_id, AdvisorOperationDraft.customer_id == user.id).with_for_update())).scalar_one_or_none()
        if draft is None:
            raise HTTPException(status_code=404, detail={"code": "DRAFT_NOT_FOUND", "message": "operation draft not found"})
        if draft.status != "pending_customer_confirmation":
            raise HTTPException(status_code=409, detail={"code": "DRAFT_STATE_CONFLICT", "message": "draft is not awaiting customer confirmation"})
        draft.status = "rejected"
        draft.customer_confirmed_at = datetime.now(timezone.utc)
        await session.commit(); await session.refresh(draft)
        return ApiResponse(data=_advisor_draft_response(draft))

@router.post("/me/operation-drafts/{draft_id}/request-revision", response_model=ApiResponse[AdvisorDraftResponse])
async def request_revision_my_operation_draft(request: Request, draft_id: str, payload: AdvisorDraftRevisionRequest, user: Annotated[User, Depends(current_user)]):
    uow = resolve_profile_uow(request)
    async with request.app.state.database.session_factory() as session:
        draft = (await session.execute(select(AdvisorOperationDraft).where(AdvisorOperationDraft.id == draft_id, AdvisorOperationDraft.customer_id == user.id).with_for_update())).scalar_one_or_none()
        if draft is None:
            raise HTTPException(status_code=404, detail={"code": "DRAFT_NOT_FOUND", "message": "operation draft not found"})
        if draft.status != "pending_customer_confirmation":
            raise HTTPException(status_code=409, detail={"code": "DRAFT_STATE_CONFLICT", "message": "draft is not awaiting customer confirmation"})
        draft.status = "revision_requested"; draft.customer_revision_note = payload.note; draft.revision_requested_at = datetime.now(timezone.utc)
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="request_revision", resource_type="advisor_operation_draft", resource_id=draft.id, detail=payload.note))
        await session.commit(); await session.refresh(draft)
        return ApiResponse(data=_advisor_draft_response(draft))
@router.post("/me/operation-drafts/{draft_id}/confirm", response_model=ApiResponse[AdvisorDraftResponse])
async def confirm_my_operation_draft(request: Request, draft_id: str, user: Annotated[User, Depends(current_user)]):
    async with request.app.state.database.session_factory() as session:
        draft = (await session.execute(select(AdvisorOperationDraft).where(AdvisorOperationDraft.id == draft_id, AdvisorOperationDraft.customer_id == user.id).with_for_update())).scalar_one_or_none()
        if draft is None:
            raise HTTPException(status_code=404, detail={"code": "DRAFT_NOT_FOUND", "message": "operation draft not found"})
        if draft.status != "pending_customer_confirmation":
            raise HTTPException(status_code=409, detail={"code": "DRAFT_STATE_CONFLICT", "message": "draft is not awaiting customer confirmation"})
        draft.status = "customer_confirmed"
        draft.customer_confirmed_at = datetime.now(timezone.utc)
        await session.commit(); await session.refresh(draft)
        return ApiResponse(data=_advisor_draft_response(draft))
@router.post("/me/operation-drafts/{draft_id}/submit-order", response_model=ApiResponse[dict])
async def submit_operation_draft_order(request: Request, draft_id: str, user: Annotated[User, Depends(current_user)]):
    trading_service = getattr(request.app.state, "trading_service", None)
    if trading_service is None:
        from app.services.trading_service import TradingService
        trading_service = TradingService()
    uow = trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        draft = (await session.execute(select(AdvisorOperationDraft).where(AdvisorOperationDraft.id == draft_id, AdvisorOperationDraft.customer_id == user.id).with_for_update())).scalar_one_or_none()
        if draft is None:
            raise HTTPException(status_code=404, detail={"code": "DRAFT_NOT_FOUND", "message": "operation draft not found"})
        if draft.status != "customer_confirmed":
            raise HTTPException(status_code=409, detail={"code": "DRAFT_STATE_CONFLICT", "message": "draft must be customer confirmed before order submission"})
        now = datetime.now(timezone.utc)
        authorization = (await session.execute(select(CustomerAdvisorAuthorization).where(
            CustomerAdvisorAuthorization.customer_id == draft.customer_id,
            CustomerAdvisorAuthorization.advisor_id == draft.advisor_id,
            CustomerAdvisorAuthorization.status == "active",
            CustomerAdvisorAuthorization.effective_from <= now,
            or_(CustomerAdvisorAuthorization.effective_to.is_(None), CustomerAdvisorAuthorization.effective_to >= now),
        ))).scalars().first()
        if authorization is None:
            raise HTTPException(status_code=403, detail={"code": "ADVISOR_AUTHORIZATION_REQUIRED", "message": "active advisor authorization required"})
        try:
            order, _ = await trading_service.create_order(session, user, draft.product_id, draft.amount, f"advisor-draft:{draft.id}", order_source="advisor_assisted", advisor_id=draft.advisor_id, confirmation_mode="advisor_customer_confirmation")
            await trading_service.transition(session, order, "pending_review", user.id, "customer confirmed in advisor flow")
        except TradingError as exc:
            raise HTTPException(status_code=409, detail={"code": "DRAFT_ORDER_SUBMISSION_FAILED", "message": str(exc)}) from exc
        draft.status = "submitted"
        draft.customer_confirmed_at = draft.customer_confirmed_at or now
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="submit", resource_type="advisor_operation_draft", resource_id=draft.id, detail=f"order_id={order.id}"))
        await session.commit(); await session.refresh(draft)
        return ApiResponse(data={"draft_id": draft.id, "status": draft.status, "order_id": order.id, "order_status": order.status})

@router.get("/me/risk-assessment", response_model=ApiResponse[RiskAssessmentResponse | None])
async def my_risk_assessment(request: Request, user: Annotated[User, Depends(current_user)]):
    uow = getattr(request.app.state, "trading_service", None)
    uow = uow.unit_of_work if uow is not None else None
    if uow is None:
        from app.repositories.unit_of_work import create_postgres_unit_of_work
        uow = create_postgres_unit_of_work()
    async with request.app.state.database.session_factory() as session:
        item = await uow.risks.get_latest_active(session, user.id)
        return ApiResponse(data=RiskAssessmentResponse(id=item.id,user_id=item.user_id,risk_level=item.risk_level,score=item.score,answers=json.loads(item.answers_json or "{}"),status=item.status,source_type=item.source_type,assessed_at=item.assessed_at,expires_at=item.expires_at) if item else None)

@router.post("/me/risk-assessment", response_model=ApiResponse[RiskAssessmentResponse])
async def submit_risk_assessment(request: Request, payload: RiskAssessmentRequest, user: Annotated[User, Depends(current_user)]):
    trading_service = getattr(request.app.state, "trading_service", None)
    uow = trading_service.unit_of_work if trading_service is not None else None
    if uow is None:
        from app.repositories.unit_of_work import create_postgres_unit_of_work
        uow = create_postgres_unit_of_work()
    async with request.app.state.database.session_factory() as session:
        item = await RiskAssessmentService(repository=uow.risks).assess(session, user.id, payload.model_dump())
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="submit", resource_type="risk_assessment", resource_id=item.id, detail=f"risk_level={item.risk_level};score={item.score}"))
        await session.commit(); await session.refresh(item)
        # 长时记忆：把测评得出的风险等级持久化为"风险偏好"（低风险、可后续供推荐参考）
        if item.risk_level:
            try:
                memory = CustomerMemoryService(request.app.state.database, request.app.state.redis.client)
                await memory.remember(customer_id=user.id, key="risk_preference", value=str(item.risk_level), memory_type="conversation_fact", source="risk_assessment")
            except Exception:  # noqa: BLE001 - 记忆失败不阻塞测评结果返回
                pass
        return ApiResponse(data=RiskAssessmentResponse(id=item.id,user_id=item.user_id,risk_level=item.risk_level,score=item.score,answers=payload.model_dump(),status=item.status,source_type=item.source_type,assessed_at=item.assessed_at,expires_at=item.expires_at))

@router.get("/me/enterprise-verification", response_model=ApiResponse[EnterpriseVerificationResponse | None])
async def my_enterprise_verification(request: Request, user: Annotated[User, Depends(current_user)]):
    trading_service = getattr(request.app.state, "trading_service", None)
    if trading_service is None:
        from app.repositories.unit_of_work import create_postgres_unit_of_work
        uow = create_postgres_unit_of_work()
    else:
        uow = trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        item = await uow.enterprise_verifications.get(session, user.id)
        return ApiResponse(data=EnterpriseVerificationResponse.model_validate(item, from_attributes=True) if item else None)

@router.post("/me/enterprise-verification", response_model=ApiResponse[EnterpriseVerificationResponse], status_code=201)
async def submit_enterprise_verification(request: Request, payload: EnterpriseVerificationRequest, user: Annotated[User, Depends(current_user)]):
    if not any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail={"code":"CUSTOMER_ACCOUNT_REQUIRED","message":"customer account required"})
    trading_service = getattr(request.app.state, "trading_service", None)
    if trading_service is None:
        from app.repositories.unit_of_work import create_postgres_unit_of_work
        uow = create_postgres_unit_of_work()
    else:
        uow = trading_service.unit_of_work
    async with request.app.state.database.session_factory() as session:
        profile = await uow.profiles.get(session, user.id)
        if profile is not None and profile.customer_type == "enterprise":
            raise HTTPException(status_code=409, detail={"code":"ALREADY_ENTERPRISE_CUSTOMER","message":"customer is already an enterprise customer"})
        item = await uow.enterprise_verifications.get(session, user.id)
        if item is not None and item.status == "approved":
            raise HTTPException(status_code=409, detail={"code":"ENTERPRISE_VERIFICATION_APPROVED","message":"enterprise verification already approved"})
        if item is None:
            item = CustomerEnterpriseVerification(id=str(uuid4()), user_id=user.id, **payload.model_dump())
            item = await uow.enterprise_verifications.save(session, item)
        else:
            for key, value in payload.model_dump().items(): setattr(item, key, value)
            item.status = "pending"; item.review_note = ""; item.reviewed_by = None; item.reviewed_at = None
            item = await uow.enterprise_verifications.update_values(session, item)
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="submit", resource_type="enterprise_verification", resource_id=item.id, detail=payload.company_name))
        await session.commit(); await session.refresh(item)
        return ApiResponse(data=EnterpriseVerificationResponse.model_validate(item, from_attributes=True))

@router.get("/me/tier", response_model=ApiResponse[CustomerTierResponse])
async def customer_tier(request: Request, user: Annotated[User, Depends(current_user)]):
    uow = resolve_profile_uow(request)
    asset_service = AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets)
    service = CustomerTierService(profile_repository=uow.profiles, asset_service=asset_service)
    async with request.app.state.database.session_factory() as session:
        data = await service.calculate(session, str(user.id), persist=True)
        await session.commit()
        return ApiResponse(data=CustomerTierResponse(**data))

@router.get("/me/recommendations", response_model=ApiResponse[RecommendationResponse])
async def recommendations(request: Request, user: Annotated[User, Depends(current_user)]):
    uow = resolve_profile_uow(request)
    asset_service = AssetSummaryService(account_repository=uow.accounts, holding_repository=uow.holdings, asset_repository=uow.assets)
    service = SuitabilityService(
        risk_repository=uow.risks,
        product_repository=uow.products,
        profile_repository=uow.profiles,
        asset_service=asset_service,
    )
    async with request.app.state.database.session_factory() as session:
        data = await service.recommend(session, str(user.id), risk_operation=await _risk_operation_for_user(session, str(user.id)))
        return ApiResponse(data=data)
