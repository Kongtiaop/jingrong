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


def _decode_access_token(token: str) -> dict:
    """解析并校验 access token，返回 payload。**HTTP 与 WebSocket 共用这一份实现**。

    为什么必须收敛到一处（ISSUE-20260915-JWT-DECODE-DUPLICATED）：
    此前 `current_user`（HTTP，`auth.py:15`）与 `websocket_current_user`（WS，`auth.py:74`）
    **各写了一遍** `jwt.decode` + `payload["type"] != "access"` 校验。
    两份实现意味着：**任何一处收紧校验，另一条通道就绕过**——
    例如将来加"token 版本/黑名单/签发者校验"，只改 HTTP 侧就会出现
    "HTTP 拒绝、WebSocket 仍放行"的**权限漏洞**，且因为两条路径从不互相报错，
    这种不一致**不会被任何测试发现**。

    故把"解析 + 类型校验"抽成单一实现，两侧只在**失败后如何处理**上不同：
      - HTTP：抛 `HTTPException(401)`（走 FastAPI 异常处理器 → JSON 响应）
      - WS：返回 `None`（WebSocket 无法返回 HTTP 状态码，由调用方关闭连接）

    返回空 dict 表示"token 不可用"，由调用方决定怎么响应。
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return {}
    if payload.get("type") != "access":
        return {}
    return payload


async def _load_active_user(request_or_app, subject: str | None) -> User | None:
    """按 sub 加载**启用中**的 User（含 roles→permissions 预加载）。"""
    app = getattr(request_or_app, "app", request_or_app)
    async with app.state.database.session_factory() as session:
        return (
            await session.execute(
                select(User)
                .options(selectinload(User.roles).selectinload(Role.permissions))
                .where(User.id == subject, User.status == "active")
            )
        ).scalar_one_or_none()


async def current_user(request: Request, credentials: Annotated[HTTPAuthorizationCredentials|None, Depends(bearer)]) -> User:
    if not credentials: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="authorization required")
    payload = _decode_access_token(credentials.credentials)
    if not payload:
        # 区分"签名/格式错"与"类型错"对排查有帮助，但对外都是 401（不泄漏细节）。
        raise HTTPException(status_code=401, detail="invalid access token")
    user = await _load_active_user(request.app, payload.get("sub"))
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
    """WebSocket 侧的认证。**与 HTTP 共用 `_decode_access_token` / `_load_active_user`**。

    与 HTTP 的唯一差异是失败形态：WebSocket 无法返回 HTTP 状态码，
    故返回 None 由调用方决定是否关闭连接（见 `customer_service` 的 ws 端点）。
    """
    token = websocket.query_params.get("access_token")
    if not token:
        return None
    payload = _decode_access_token(token)
    if not payload:
        return None
    return await _load_active_user(websocket.app, payload.get("sub"))
