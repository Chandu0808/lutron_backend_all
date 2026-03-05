from pydantic import BaseModel, Field, field_validator
from typing import Literal, List, Optional

# ----- Role & Permission literals -----
RoleLiteral = Literal["Superadmin", "Admin", "Operator"]
PermLiteral = Literal["monitor", "monitor_control", "monitor_control_edit"]

# ----- Permission payload for per-floor assignment -----
class UserPermissionCreate(BaseModel):
    floor_id: int
    floor_permission: PermLiteral  # keep name aligned with enum meaning

    @field_validator("floor_permission", mode="before")
    @classmethod
    def normalize_perm(cls, v: str) -> str:
        return str(v).strip().lower()

# ----- User create payload -----
class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: RoleLiteral

    # Back-compat: accept front-end key "floor" but expose as "permissions" in code
    permissions: Optional[List[UserPermissionCreate]] = Field(default=None, alias="floor")

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, v: str) -> str:
        v = str(v).strip().lower()
        mapping = {"superadmin": "Superadmin", "admin": "Admin", "operator": "Operator"}
        if v not in mapping:
            raise ValueError("role must be Superadmin, Admin, or Operator")
        return mapping[v]

    class Config:
        populate_by_name = True  # allows setting by field name

# ----- Login payloads (REST of your code imports this) -----
class LoginRequest(BaseModel):
    username: str
    password: str

# ----- Change password payload -----
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str