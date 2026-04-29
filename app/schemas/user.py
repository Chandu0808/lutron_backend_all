from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
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

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v: str) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("name cannot be empty")
        return s

    model_config = ConfigDict(populate_by_name=True)


class UserUpdate(BaseModel):
    """
    Partial update for an active user. ``role`` is immutable and must not appear in the body
    (extra fields are rejected).

    ``name`` and ``email`` may be updated when provided; each must be unique among active users.

    For Operators only: if ``permissions`` (alias ``floor``) is present, all existing
    ``user_permissions`` rows are replaced with the given set after floor validation.
    If ``permissions`` is omitted, floor assignments are unchanged.
    """
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    permissions: Optional[List[UserPermissionCreate]] = Field(default=None, alias="floor")

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("name cannot be empty when provided")
        return s

    @field_validator("email", mode="before")
    @classmethod
    def normalize_optional_email(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            raise ValueError("email cannot be empty when provided")
        return s

    @field_validator("password", mode="before")
    @classmethod
    def empty_password_means_omit(cls, v):
        if v is None:
            return None
        if str(v).strip() == "":
            return None
        return v


# ----- Login payloads (REST of your code imports this) -----
class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, v: str) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("username cannot be empty")
        return s

# ----- Change password payload -----
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str