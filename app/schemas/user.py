from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


UserRole = Literal["admin", "user", "viewer"]


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6)
    role: UserRole = "user"

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return str(value or "").strip().lower()


class UserAdminUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[UserRole] = None


class AllowlistCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    role: UserRole = "user"

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return str(value or "").strip().lower()


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
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
