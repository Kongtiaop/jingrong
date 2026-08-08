from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload
from datetime import datetime, timezone
from uuid import uuid4

from app.common.response import ApiResponse
from app.common.security.auth import current_user, require_permission
from app.models.audit import AuditLog
from app.models.auth import Permission, Role, User
from app.models.operations import BusinessOperation
from app.models.trading import Account, Order, RiskAlert, Trade
from app.models.profile import CustomerEnterpriseVerification, CustomerProfile, CustomerRiskAssessment, Product
from app.schemas.admin import *
from app.schemas.profile import EnterpriseVerificationResponse

router = APIRouter(prefix="/admin", tags=["admin"])

def resolve_admin_uow(request: Request):
    trading_service = getattr(request.app.state, "trading_service", None)
    if trading_service is not None:
        return trading_service.unit_of_work
    from app.repositories.unit_of_work import create_postgres_unit_of_work
    return create_postgres_unit_of_work()



def ensure_admin(user: User) -> User:
    if not user.is_super_admin and not any(role.code == "super_admin" for role in user.roles):
        raise HTTPException(status_code=403, detail={"code":"ADMIN_PERMISSION_REQUIRED","message":"admin permission required"})
    return user


def user_response(user: User) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        is_super_admin=user.is_super_admin,
        roles=[role.code for role in user.roles],
        created_at=user.created_at,
        updated_at=user.updated_at,
        deleted_at=user.deleted_at,
    )


@router.get("/dashboard/risk-workbench", response_model=ApiResponse[dict])
async def risk_workbench_dashboard(request: Request, user: Annotated[User, Depends(current_user)] = None):
    if any(role.code == "customer" for role in user.roles):
        raise HTTPException(status_code=403, detail={"code":"STAFF_REQUIRED","message":"staff account required"})
    permissions = {permission.code for role in user.roles for permission in role.permissions}
    if not user.is_super_admin and not ({"order:review", "operation:read_staff", "risk:alert_read", "risk:alert_action", "risk:scan_execute", "audit:read"} & permissions):
        raise HTTPException(status_code=403, detail={"code":"DASHBOARD_PERMISSION_REQUIRED","message":"dashboard permission required"})
    async with request.app.state.database.session_factory() as session:
        async def count(model, *criteria):
            return int((await session.execute(select(func.count()).select_from(model).where(*criteria))).scalar_one())
        pending_orders = await count(Order, Order.status == "pending_review")
        pending_redeems = await count(BusinessOperation, BusinessOperation.operation_type == "redeem", BusinessOperation.status == "pending_review")
        pending_transfers = await count(BusinessOperation, BusinessOperation.operation_type == "transfer", BusinessOperation.status == "pending_review")
        restricted_accounts = await count(Account, Account.risk_restriction_status == "restricted")
        open_alerts = await count(RiskAlert, RiskAlert.status.in_(["open", "acknowledged", "investigating", "report_pending"]))
        customer_total = await count(User, User.status == "active", User.roles.any(code="customer"))
        today_amount = (await session.execute(select(func.coalesce(func.sum(Trade.amount), 0)))).scalar_one()
        risk_rows = (await session.execute(select(CustomerRiskAssessment.risk_level, func.count()).where(CustomerRiskAssessment.status == "active").group_by(CustomerRiskAssessment.risk_level))).all()
        risk_map = {str(level): int(total) for level, total in risk_rows}
        risk_distribution = [{"level": level, "count": risk_map.get(level, 0)} for level in ["C1", "C2", "C3", "C4", "C5"]]
        level_rank = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}
        pending_rows = (await session.execute(select(Order, Product, Account, CustomerRiskAssessment).join(Product, Product.id == Order.product_id).join(Account, Account.id == Order.account_id).outerjoin(CustomerRiskAssessment, (CustomerRiskAssessment.user_id == Order.user_id) & (CustomerRiskAssessment.status == "active")).where(Order.status == "pending_review"))).all()
        suitability = {"suitable": 0, "mismatch": 0, "restricted": 0, "missing_risk": 0}
        for order, product, account, assessment in pending_rows:
            if account.risk_restriction_status == "restricted":
                suitability["restricted"] += 1
                continue
            actual = level_rank.get(str(assessment.risk_level).upper().replace("R", "C"), 0) if assessment else 0
            required = level_rank.get(str(product.risk_level).upper().replace("R", "C"), 1)
            if actual == 0:
                suitability["missing_risk"] += 1
            elif actual < required:
                suitability["mismatch"] += 1
            else:
                suitability["suitable"] += 1
        operation_restrictions = {"redeems": await count(BusinessOperation, BusinessOperation.operation_type == "redeem", BusinessOperation.status == "pending_review", BusinessOperation.user_id.in_(select(Account.user_id).where(Account.risk_restriction_status == "restricted"))), "transfers": await count(BusinessOperation, BusinessOperation.operation_type == "transfer", BusinessOperation.status == "pending_review", BusinessOperation.user_id.in_(select(Account.user_id).where(Account.risk_restriction_status == "restricted")))}
        order_rows = (await session.execute(select(Order.status, func.count()).group_by(Order.status))).all()
        operation_rows = (await session.execute(select(BusinessOperation.operation_type, BusinessOperation.status, func.count()).group_by(BusinessOperation.operation_type, BusinessOperation.status))).all()
        alert_rows = (await session.execute(select(RiskAlert.status, func.count()).group_by(RiskAlert.status))).all()
        order_status = {str(status): int(total) for status, total in order_rows}
        operation_status = {f"{operation_type}:{status}": int(total) for operation_type, status, total in operation_rows}
        alert_status = {str(status): int(total) for status, total in alert_rows}
        return ApiResponse(data={
            "todo": {"pending_orders": pending_orders, "pending_redeems": pending_redeems, "pending_transfers": pending_transfers, "restricted_accounts": restricted_accounts, "open_alerts": open_alerts},
            "suitability": suitability,
            "operation_restrictions": operation_restrictions,
            "risk_distribution": risk_distribution,
            "flows": {"orders": order_status, "redeems": {key.split(":", 1)[1]: value for key, value in operation_status.items() if key.startswith("redeem:")}, "transfers": {key.split(":", 1)[1]: value for key, value in operation_status.items() if key.startswith("transfer:")}},
            "alert_status": alert_status,
            "supporting": {"customer_total": customer_total, "executed_trade_amount": float(today_amount or 0)},
        })

@router.get("/users", response_model=ApiResponse[AdminUserListResponse])
async def list_users(request: Request, q: str = Query(""), role: str = Query(""), status: str = Query("active", pattern="^(active|deleted|all)$"), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        total, users = await uow.admin.list_users(session, q=q, role=role, status=status, limit=limit, offset=offset)
        return ApiResponse(data=AdminUserListResponse(total=total, items=[user_response(item) for item in users]))


@router.get("/enterprise-verifications", response_model=ApiResponse[EnterpriseVerificationListResponse])
async def list_enterprise_verifications(request: Request, status: str = Query("pending"), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        rows = await uow.enterprise_verifications.list(session, status=status)
        total = len(rows)
        page_rows = rows[offset:offset + limit]
        items = [EnterpriseVerificationResponse.model_validate(row, from_attributes=True).model_dump(mode="json") for row in page_rows]
        return ApiResponse(data=EnterpriseVerificationListResponse(items=items, total=total))

@router.post("/enterprise-verifications/{verification_id}/review", response_model=ApiResponse[dict])
async def review_enterprise_verification(request: Request, verification_id: str, payload: EnterpriseVerificationReviewRequest, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        item = await uow.enterprise_verifications.get_by_id(session, verification_id)
        if item is None: raise HTTPException(status_code=404, detail={"code":"VERIFICATION_NOT_FOUND","message":"verification not found"})
        if item.status == "approved" and payload.approved: raise HTTPException(status_code=409, detail={"code":"VERIFICATION_ALREADY_APPROVED","message":"verification already approved"})
        item.status = "approved" if payload.approved else "rejected"; item.review_note = payload.note; item.reviewed_by = user.id; item.reviewed_at = datetime.now(timezone.utc)
        if payload.approved:
            profile = await uow.profiles.get(session, item.user_id)
            if profile is None:
                profile = await uow.profiles.save(session, CustomerProfile(id=str(uuid4()), user_id=item.user_id))
            profile.customer_type = "enterprise"; profile.customer_tier = "enterprise_standard"; profile.source_type = "enterprise_verified"
            await uow.profiles.update_values(session, profile)
        item = await uow.enterprise_verifications.update_values(session, item)
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="approve" if payload.approved else "reject", resource_type="enterprise_verification", resource_id=item.id, detail=payload.note))
        await session.commit(); await session.refresh(item)
        return ApiResponse(data=EnterpriseVerificationResponse.model_validate(item, from_attributes=True).model_dump(mode="json"))

@router.get("/roles", response_model=ApiResponse[list[AdminRoleResponse]])
async def list_roles(request: Request, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        roles = await uow.admin.list_roles(session)
        return ApiResponse(data=[AdminRoleResponse(id=role.id, code=role.code, name=role.name, permissions=[permission.code for permission in role.permissions]) for role in roles])


@router.get("/recycle-bin", response_model=ApiResponse[dict])
async def recycle_bin(request: Request, module: str = Query("user", pattern="^(user|product)$"), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        if module == "user":
            query = select(User).options(selectinload(User.roles)).where(User.deleted_at.is_not(None), User.status == "deleted").order_by(User.deleted_at.desc())
            total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
            rows = list((await session.execute(query.offset(offset).limit(limit))).scalars().all())
            items = [{"id": row.id, "name": row.display_name, "key": row.username, "status": row.status, "created_at": row.created_at, "updated_at": row.updated_at, "deleted_at": row.deleted_at, "roles": [role.code for role in row.roles]} for row in rows]
        else:
            from app.models.profile import Product
            query = select(Product).where(Product.deleted_at.is_not(None), Product.status == "deleted").order_by(Product.deleted_at.desc())
            total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
            rows = list((await session.execute(query.offset(offset).limit(limit))).scalars().all())
            items = [{"id": row.id, "name": row.name, "key": row.product_type, "status": row.status, "created_at": row.created_at, "updated_at": row.updated_at, "deleted_at": row.deleted_at, "risk_level": row.risk_level} for row in rows]
        return ApiResponse(data={"items": items, "total": total, "module": module})


@router.get("/audit-logs", response_model=ApiResponse[dict])
async def audit_logs(request: Request, action: str = Query(""), resource_type: str = Query(""), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("audit:read"))] = None):
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        total, rows = await uow.audit_logs.list(session, action=action, resource_type=resource_type, limit=limit, offset=offset)
        actor_ids = {row.actor_user_id for row in rows if row.actor_user_id}
        actors = {row.id: row for row in (await session.execute(select(User).where(User.id.in_(actor_ids)))).scalars().all()} if actor_ids else {}
        operation_ids = {row.resource_id for row in rows if row.resource_type == "business_operation"}
        operations = {row.id: row for row in (await session.execute(select(BusinessOperation).where(BusinessOperation.id.in_(operation_ids)))).scalars().all()} if operation_ids else {}
        customer_ids = {operation.user_id for operation in operations.values() if operation.user_id}
        customer_ids.update(row.resource_id for row in rows if row.resource_type == "user")
        customers = {row.id: row for row in (await session.execute(select(User).where(User.id.in_(customer_ids)))).scalars().all()} if customer_ids else {}
        product_ids = {operation.product_id for operation in operations.values() if operation.product_id}
        products = {row.id: row for row in (await session.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()} if product_ids else {}
        items = []
        for row in rows:
            actor = actors.get(row.actor_user_id)
            operation = operations.get(row.resource_id)
            customer = customers.get(operation.user_id) if operation else customers.get(row.resource_id) if row.resource_type == "user" else None
            product = products.get(operation.product_id) if operation and operation.product_id else None
            business_type = operation.operation_type if operation else row.resource_type
            business_status = operation.status if operation else None
            business_reference = operation.id if operation else row.resource_id
            if operation and operation.operation_type == "transfer":
                resource_name = f"转账至 {operation.to_account}" if operation.to_account else "转账业务"
            elif product is not None:
                resource_name = product.name
            elif customer is not None:
                resource_name = customer.display_name
            else:
                resource_name = row.resource_id
            items.append({
                "id": row.id,
                "actor_user_id": row.actor_user_id,
                "actor_name": actor.display_name if actor else "系统",
                "action": row.action,
                "resource_type": row.resource_type,
                "resource_id": row.resource_id,
                "resource_name": resource_name,
                "customer_name": customer.display_name if customer else None,
                "customer_username": customer.username if customer else None,
                "business_type": business_type,
                "business_status": business_status,
                "business_reference": business_reference,
                "product_name": product.name if product else None,
                "detail": row.detail,
                "created_at": row.created_at,
            })
        return ApiResponse(data={"items": items, "total": total})



@router.get("/permissions", response_model=ApiResponse[list[AdminPermissionResponse]])
async def list_permissions(request: Request, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        rows = await uow.admin.list_permissions(session)
        return ApiResponse(data=[AdminPermissionResponse(id=permission.id, code=permission.code, name=permission.name) for permission in rows])


@router.put("/users/{user_id}/roles", response_model=ApiResponse[AdminUserResponse])
async def update_user_roles(request: Request, user_id: str, payload: AdminRoleUpdateRequest, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    if user.id == user_id and "super_admin" not in payload.roles:
        raise HTTPException(status_code=400, detail={"code":"SELF_SUPER_ADMIN_ROLE_FORBIDDEN","message":"cannot remove your own super_admin role"})
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        target = await uow.admin.get_user(session, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail={"code":"USER_NOT_FOUND","message":"user not found"})
        roles = await uow.admin.get_roles(session, payload.roles)
        if len(roles) != len(set(payload.roles)):
            raise HTTPException(status_code=400, detail={"code":"UNKNOWN_ROLE","message":"unknown role"})
        target = await uow.admin.update_user_roles(session, target, roles)
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="update_roles", resource_type="user", resource_id=target.id, detail=",".join(payload.roles)))
        await session.commit()
        await session.refresh(target)
        return ApiResponse(data=user_response(target))


@router.put("/users/{user_id}/status", response_model=ApiResponse[AdminUserResponse])
async def update_user_status(request: Request, user_id: str, status: str = Query(..., pattern="^(active|disabled)$"), user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    if user.id == user_id and status != "active":
        raise HTTPException(status_code=400, detail={"code":"SELF_ACCOUNT_DISABLE_FORBIDDEN","message":"cannot disable your own account"})
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        target = await uow.admin.get_user(session, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail={"code":"USER_NOT_FOUND","message":"user not found"})
        target = await uow.admin.update_user_status(session, target, status)
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="update_status", resource_type="user", resource_id=target.id, detail=status))
        await session.commit()
        await session.refresh(target)
        return ApiResponse(data=user_response(target))


@router.delete("/users/{user_id}", response_model=ApiResponse[AdminUserResponse])
async def soft_delete_user(request: Request, user_id: str, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    if user.id == user_id:
        raise HTTPException(status_code=400, detail={"code":"SELF_ACCOUNT_DELETE_FORBIDDEN","message":"cannot delete your own account"})
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        target = await uow.admin.get_user(session, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail={"code":"USER_NOT_FOUND","message":"user not found"})
        target.status = "deleted"
        target.deleted_at = datetime.now(timezone.utc)
        await uow.admin.update_user_status(session, target, "deleted")
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="soft_delete", resource_type="user", resource_id=target.id, detail=target.username))
        await session.commit()
        await session.refresh(target)
        return ApiResponse(data=user_response(target))


@router.put("/users/{user_id}/restore", response_model=ApiResponse[AdminUserResponse])
async def restore_user(request: Request, user_id: str, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        target = await uow.admin.get_user(session, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail={"code":"USER_NOT_FOUND","message":"user not found"})
        target.status = "active"
        target.deleted_at = None
        await uow.admin.update_user_status(session, target, "active")
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="restore", resource_type="user", resource_id=target.id, detail=target.username))
        await session.commit()
        await session.refresh(target)
        return ApiResponse(data=user_response(target))


@router.put("/roles/{role_id}/permissions", response_model=ApiResponse[AdminRoleResponse])
async def update_role_permissions(request: Request, role_id: str, payload: AdminRolePermissionsUpdateRequest, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    uow = resolve_admin_uow(request)
    async with request.app.state.database.session_factory() as session:
        role = await uow.admin.get_role_by_id(session, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail={"code":"ROLE_NOT_FOUND","message":"role not found"})
        permissions = await uow.admin.get_permissions(session, payload.permissions)
        if len(permissions) != len(set(payload.permissions)):
            raise HTTPException(status_code=400, detail={"code":"UNKNOWN_PERMISSION","message":"unknown permission"})
        if role.code == "super_admin":
            raise HTTPException(status_code=400, detail={"code":"SUPER_ADMIN_PERMISSIONS_FIXED","message":"super_admin permissions are fixed"})
        await uow.admin.update_role_permissions(session, role, permissions)
        await uow.audit_logs.save(session, AuditLog(id=str(uuid4()), actor_user_id=user.id, action="update_permissions", resource_type="role", resource_id=role.id, detail=",".join(payload.permissions)))
        await session.commit()
        await session.refresh(role)
        return ApiResponse(data=AdminRoleResponse(id=role.id, code=role.code, name=role.name, permissions=[permission.code for permission in role.permissions]))








