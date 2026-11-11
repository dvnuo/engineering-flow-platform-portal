from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# "viewer" was removed: no permission check ever distinguished it from "user"
# (every check is `role == "admin" or owner`), so assigning it promised a
# read-only account that did not exist. "admin" is a system administrator,
# "group_admin" administers their own group (app/services/authorization.py).
UserRole = Literal["admin", "group_admin", "user"]


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6)
    role: UserRole = "user"
    # Required from a system administrator: every member joins a group.
    group_id: Optional[int] = None

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return str(value or "").strip().lower()


class UserAdminUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[UserRole] = None
    # Moves the member to another group (system administrators only).
    group_id: Optional[int] = None


MEMBER_BULK_MOVE_MAX = 1000


class MemberBulkMoveRequest(BaseModel):
    """Several members to one group at once (User Management, the selected rows)."""

    model_config = ConfigDict(extra="forbid")

    user_ids: list[int] = Field(..., min_length=1, max_length=MEMBER_BULK_MOVE_MAX)
    group_id: int

    @field_validator("user_ids")
    @classmethod
    def unique_user_ids(cls, value):
        return list(dict.fromkeys(value))


class MemberBulkMoveResponse(BaseModel):
    group_id: int
    group_name: str
    # Usernames, in the order the ids were sent.
    moved: list[str] = Field(default_factory=list)
    # Already in the group: nothing to do.
    unchanged: list[str] = Field(default_factory=list)
    # Group administrators who became members of the new group.
    demoted: list[str] = Field(default_factory=list)
    # Members who must sign in to the model provider again: they had chosen
    # their own model and the old group's shared credential was dropped
    # (group_service.member_llm_after_group_change).
    llm_needs_attention: list[str] = Field(default_factory=list)
    # Moves that raised and were rolled back; the rest went ahead.
    failed: list[str] = Field(default_factory=list)
    # Ids with no account.
    not_found: list[int] = Field(default_factory=list)


class AllowlistCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    role: UserRole = "user"
    # The group the member joins: required from a system administrator, a
    # group administrator's own group otherwise.
    group_id: Optional[int] = None

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return str(value or "").strip().lower()


class AllowlistBulkCreateRequest(BaseModel):
    usernames: list[str] = Field(..., min_length=1, max_length=200)
    role: UserRole = "user"
    group_id: Optional[int] = None

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
    # Usernames a group administrator may not add: someone in another group.
    refused: list[str] = []


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    group_id: Optional[int] = None
    nickname: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AllowlistResponse(BaseModel):
    id: int
    username: str
    role: str
    group_id: Optional[int] = None
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
    group_id: Optional[int] = None
    group_name: Optional[str] = None
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
    # Over the overview's period (``period_days``): runs, chats, the AI-credits
    # estimate and tokens the runtimes reported, and days with any run.
    execution_count_period: int = 0
    chat_count_period: int = 0
    credits_period: float = 0.0
    input_tokens_period: int = 0
    output_tokens_period: int = 0
    active_days_period: int = 0


class MemberOverviewResponse(BaseModel):
    users: list[MemberUsageResponse]
    allowlist_entries: list[AllowlistResponse]
    pending_allowlist_entries: list[AllowlistResponse]
    # The overview of one group (a group administrator's), or None for all.
    group_id: Optional[int] = None
    groups: list[dict] = []
    period_days: int = 30
    summary: dict[str, int | float]
