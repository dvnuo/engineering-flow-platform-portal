from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# "viewer" was removed: no permission check ever distinguished it from "user"
# (every check is `role == "admin" or owner`), so assigning it promised a
# read-only account that did not exist.
UserRole = Literal["admin", "user"]


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


class AllowlistBulkCreateRequest(BaseModel):
    usernames: list[str] = Field(..., min_length=1, max_length=200)
    role: UserRole = "user"

    @field_validator("usernames", mode="before")
    @classmethod
    def normalize_usernames(cls, value):
        if isinstance(value, str):
            value = value.splitlines()
        if not isinstance(value, (list, tuple)):
            raise ValueError("Usernames must be a list or newline-separated text")

        usernames = []
        seen = set()
        for raw_username in value:
            username = str(raw_username or "").strip().lower()
            if not username:
                continue
            if not 3 <= len(username) <= 64:
                raise ValueError("Each username must be between 3 and 64 characters")
            if username not in seen:
                seen.add(username)
                usernames.append(username)
        return usernames


class AllowlistBulkResponse(BaseModel):
    added: list[str]
    already_allowlisted: list[str]


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
