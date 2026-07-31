from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from datetime import datetime, timezone
from sqlalchemy.orm import selectinload
from app.common.response import ApiResponse
from app.common.security.auth import require_permission
from app.models.auth import Permission, Role, User
from app.schemas.admin import *

router = APIRouter(prefix="/admin", tags=["admin"])

def ensure_admin(user: User) -> User:
    if not user.is_super_admin and not any(role.code == "super_admin" for role in user.roles):
        raise HTTPException(status_code=403, detail="admin permission required")
    return user

@router.get("/users", response_model=ApiResponse[AdminUserListResponse])
async def list_users(request: Request, q: str = Query(""), role: str = Query(""), status: str = Query("active", pattern="^(active|deleted|all)$"), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        query = select(User).options(selectinload(User.roles)).order_by(User.created_at.desc())
        if status == "active": query = query.where(User.deleted_at.is_(None), User.status != "deleted")
        elif status == "deleted": query = query.where(User.deleted_at.is_not(None), User.status == "deleted")
        if q.strip():
            needle=f"%{q.strip()}%"; query=query.where(or_(User.username.ilike(needle), User.display_name.ilike(needle)))
        if role.strip():
            query = query.where(User.roles.any(code=role.strip()))
        total=(await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
        users=list((await session.execute(query.offset(offset).limit(limit))).scalars().all())
        return ApiResponse(data=AdminUserListResponse(total=total, items=[AdminUserResponse(id=u.id,username=u.username,display_name=u.display_name,status=u.status,is_super_admin=u.is_super_admin,roles=[r.code for r in u.roles]) for u in users]))

@router.get("/roles", response_model=ApiResponse[list[AdminRoleResponse]])
async def list_roles(request: Request, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        roles=list((await session.execute(select(Role).options(selectinload(Role.permissions)).order_by(Role.code))).scalars().all())
        return ApiResponse(data=[AdminRoleResponse(id=r.id,code=r.code,name=r.name,permissions=[p.code for p in r.permissions]) for r in roles])

@router.get("/permissions", response_model=ApiResponse[list[AdminPermissionResponse]])
async def list_permissions(request: Request, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        rows=list((await session.execute(select(Permission).order_by(Permission.code))).scalars().all())
        return ApiResponse(data=[AdminPermissionResponse(id=p.id,code=p.code,name=p.name) for p in rows])

@router.put("/users/{user_id}/roles", response_model=ApiResponse[AdminUserResponse])
async def update_user_roles(request: Request, user_id: str, payload: AdminRoleUpdateRequest, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    if user.id == user_id and "super_admin" not in payload.roles:
        raise HTTPException(status_code=400, detail="cannot remove your own super_admin role")
    async with request.app.state.database.session_factory() as session:
        target=(await session.execute(select(User).options(selectinload(User.roles)).where(User.id==user_id))).scalar_one_or_none()
        if target is None: raise HTTPException(status_code=404, detail="user not found")
        roles=list((await session.execute(select(Role).where(Role.code.in_(payload.roles)))).scalars().all())
        if len(roles)!=len(set(payload.roles)): raise HTTPException(status_code=400, detail="unknown role")
        target.roles=roles; target.is_super_admin="super_admin" in payload.roles; await session.commit()
        target=(await session.execute(select(User).options(selectinload(User.roles)).where(User.id==user_id))).scalar_one()
        return ApiResponse(data=AdminUserResponse(id=target.id,username=target.username,display_name=target.display_name,status=target.status,is_super_admin=target.is_super_admin,roles=[r.code for r in target.roles]))

@router.put("/users/{user_id}/status", response_model=ApiResponse[AdminUserResponse])
async def update_user_status(request: Request, user_id: str, status: str = Query(..., pattern="^(active|disabled)$"), user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    if user.id == user_id and status != "active":
        raise HTTPException(status_code=400, detail="cannot disable your own account")
    async with request.app.state.database.session_factory() as session:
        target=(await session.execute(select(User).options(selectinload(User.roles)).where(User.id==user_id))).scalar_one_or_none()
        if target is None: raise HTTPException(status_code=404, detail="user not found")
        target.status=status
        await session.commit()
        target=(await session.execute(select(User).options(selectinload(User.roles)).where(User.id==user_id))).scalar_one()
        return ApiResponse(data=AdminUserResponse(id=target.id,username=target.username,display_name=target.display_name,status=target.status,is_super_admin=target.is_super_admin,roles=[r.code for r in target.roles]))


@router.delete("/users/{user_id}", response_model=ApiResponse[AdminUserResponse])
async def soft_delete_user(request: Request, user_id: str, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    if user.id == user_id:
        raise HTTPException(status_code=400, detail="cannot delete your own account")
    async with request.app.state.database.session_factory() as session:
        target=(await session.execute(select(User).options(selectinload(User.roles)).where(User.id==user_id))).scalar_one_or_none()
        if target is None: raise HTTPException(status_code=404, detail="user not found")
        target.status="deleted"; target.deleted_at=datetime.now(timezone.utc)
        await session.commit()
        return ApiResponse(data=AdminUserResponse(id=target.id,username=target.username,display_name=target.display_name,status=target.status,is_super_admin=target.is_super_admin,roles=[r.code for r in target.roles]))

@router.put("/users/{user_id}/restore", response_model=ApiResponse[AdminUserResponse])
async def restore_user(request: Request, user_id: str, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        target=(await session.execute(select(User).options(selectinload(User.roles)).where(User.id==user_id))).scalar_one_or_none()
        if target is None: raise HTTPException(status_code=404, detail="user not found")
        target.status="active"; target.deleted_at=None
        await session.commit()
        return ApiResponse(data=AdminUserResponse(id=target.id,username=target.username,display_name=target.display_name,status=target.status,is_super_admin=target.is_super_admin,roles=[r.code for r in target.roles]))
@router.put("/roles/{role_id}/permissions", response_model=ApiResponse[AdminRoleResponse])
async def update_role_permissions(request: Request, role_id: str, payload: AdminRolePermissionsUpdateRequest, user: Annotated[User, Depends(require_permission("admin:write"))] = None):
    ensure_admin(user)
    async with request.app.state.database.session_factory() as session:
        role = (await session.execute(select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id))).scalar_one_or_none()
        if role is None:
            raise HTTPException(status_code=404, detail="role not found")
        permissions = list((await session.execute(select(Permission).where(Permission.code.in_(payload.permissions)))).scalars().all())
        if len(permissions) != len(set(payload.permissions)):
            raise HTTPException(status_code=400, detail="unknown permission")
        if role.code == "super_admin":
            raise HTTPException(status_code=400, detail="super_admin permissions are fixed")
        role.permissions = permissions
        await session.commit()
        role = (await session.execute(select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id))).scalar_one()
        return ApiResponse(data=AdminRoleResponse(id=role.id, code=role.code, name=role.name, permissions=[p.code for p in role.permissions]))




