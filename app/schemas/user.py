from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


UserRole = Literal["admin", "user", "viewer"]


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6)
    role: UserRole = "user"


class PasswordUpdateRequest(BaseModel):
    password: str = Field(..., min_length=6)


class UserAdminUpdateRequest(BaseModel):
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class AllowlistCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    role: UserRole = "user"


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    nickname: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AllowlistResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    added_by_user_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MemberUsageResponse(BaseModel):
    id: int
    username: str
    nickname: Optional[str] = None
    role: str
    is_active: bool
    is_allowlisted: bool
    allowlist_entry_id: Optional[int] = None
    created_at: datetime
    last_login_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    assistant_count: int
    task_count: int
    completed_task_count: int
    execution_count: int
    chat_count: int
    delegation_count: int


class MemberOverviewResponse(BaseModel):
    users: list[MemberUsageResponse]
    allowlist_entries: list[AllowlistResponse]
    pending_allowlist_entries: list[AllowlistResponse]
    summary: dict[str, int]
