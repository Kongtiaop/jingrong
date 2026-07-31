from pydantic import BaseModel, Field
class AdminUserResponse(BaseModel):
    id:str; username:str; display_name:str; status:str; is_super_admin:bool; roles:list[str]
class AdminUserListResponse(BaseModel):
    items:list[AdminUserResponse]; total:int
class AdminRoleResponse(BaseModel):
    id:str; code:str; name:str; permissions:list[str]
class AdminPermissionResponse(BaseModel):
    id:str; code:str; name:str
class AdminRoleUpdateRequest(BaseModel):
    roles:list[str]=Field(min_length=1)
