from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from app.common.response import ApiResponse
from app.common.security.auth import current_user
from app.models.auth import User
from app.schemas.auth import LoginRequest, MeResponse, RefreshRequest, RegisterRequest, RegisterResponse, TokenResponse

router=APIRouter(prefix="/auth",tags=["auth"])
@router.post("/login",response_model=ApiResponse[TokenResponse])
async def login(request:Request,payload:LoginRequest):
 try: data=await request.app.state.auth_service.login(payload.username,payload.password)
 except ValueError as exc: raise HTTPException(status_code=401,detail=str(exc)) from exc
 return ApiResponse(data=TokenResponse(**data))
@router.post("/refresh",response_model=ApiResponse[TokenResponse])
async def refresh(request:Request,payload:RefreshRequest):
 try: data=await request.app.state.auth_service.refresh(payload.refresh_token)
 except ValueError as exc: raise HTTPException(status_code=401,detail=str(exc)) from exc
 return ApiResponse(data=TokenResponse(**data))
@router.post("/logout",response_model=ApiResponse[dict])
async def logout(request:Request,payload:RefreshRequest):
 await request.app.state.auth_service.revoke(payload.refresh_token)
 return ApiResponse(data={"logged_out":True})
@router.get("/me",response_model=ApiResponse[MeResponse])
async def me(user: Annotated[User,Depends(current_user)]):
 roles=[r.code for r in user.roles]; permissions=sorted({p.code for r in user.roles for p in r.permissions})
 return ApiResponse(data=MeResponse(id=str(user.id),username=user.username,display_name=user.display_name,roles=roles,permissions=permissions,is_super_admin=user.is_super_admin))

@router.post("/register", response_model=ApiResponse[RegisterResponse], status_code=201)
async def register(request: Request, payload: RegisterRequest):
    try:
        data = await request.app.state.auth_service.register(payload)
    except ValueError as exc:
        status_code = 409 if "exists" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return ApiResponse(data=data)
