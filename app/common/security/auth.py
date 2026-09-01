from __future__ import annotations
from typing import Annotated
import jwt
from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.settings import get_settings
from app.models.auth import Permission, Role, User

bearer=HTTPBearer(auto_error=False)
async def current_user(request: Request, credentials: Annotated[HTTPAuthorizationCredentials|None, Depends(bearer)]) -> User:
    if not credentials: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="authorization required")
    settings=get_settings()
    try: payload=jwt.decode(credentials.credentials,settings.jwt_secret,algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc: raise HTTPException(status_code=401,detail="invalid access token") from exc
    if payload.get("type") != "access": raise HTTPException(status_code=401,detail="invalid token type")
    async with request.app.state.database.session_factory() as session:
        user=(await session.execute(select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id==payload.get("sub"),User.status=="active"))).scalar_one_or_none()
        if not user: raise HTTPException(status_code=401,detail="user inactive")
        return user

def require_permission(code: str):
    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.is_super_admin: return user
        permissions={p.code for role in user.roles for p in role.permissions}
        if code not in permissions: raise HTTPException(status_code=403,detail="permission denied")
        return user
    return dependency

def require_staff_permission(code: str):
    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.is_super_admin:
            return user
        permissions = {p.code for role in user.roles for p in role.permissions}
        if code not in permissions or any(role.code == "customer" for role in user.roles):
            raise HTTPException(status_code=403, detail="staff permission required")
        return user
    return dependency


async def staff_customer_user(user: Annotated[User, Depends(current_user)]) -> User:
    return await require_staff_permission("customer:read_all")(user)



def require_advisor_permission(code: str):
    async def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if user.is_super_admin:
            return user
        role_codes = {role.code for role in user.roles}
        permissions = {permission.code for role in user.roles for permission in role.permissions}
        if "advisor" not in role_codes or code not in permissions:
            raise HTTPException(status_code=403, detail="advisor permission required")
        return user
    return dependency


async def advisor_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.is_super_admin:
        return user
    role_codes = {role.code for role in user.roles}
    if "advisor" not in role_codes:
        raise HTTPException(status_code=403, detail="advisor permission required")
    return user


async def websocket_current_user(websocket: WebSocket) -> User | None:
    token = websocket.query_params.get("access_token")
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    async with websocket.app.state.database.session_factory() as session:
        return (await session.execute(select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == payload.get("sub"), User.status == "active"))).scalar_one_or_none()
