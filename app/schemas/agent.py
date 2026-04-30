from datetime import datetime

from pydantic import BaseModel, field_validator
from typing import Optional

from app.utils.git_urls import normalize_git_repo_url

ALLOWED_AGENT_TYPES = {"workspace", "specialist", "task"}


class AgentCreateRequest(BaseModel):
    name: str
    image: Optional[str] = None
    repo_url: Optional[str] = None  # deprecated, ignored
    branch: Optional[str] = None  # deprecated, ignored
    skill_repo_url: Optional[str] = None
    skill_branch: Optional[str] = None
    disk_size_gi: int = 20
    mount_path: str = "/root/.efp"
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None
    agent_type: str = "workspace"
    capability_profile_id: Optional[str] = None
    policy_profile_id: Optional[str] = None
    runtime_profile_id: Optional[str] = None

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_AGENT_TYPES:
            raise ValueError("agent_type must be one of: workspace, specialist, task")
        return normalized

    @field_validator("repo_url", "skill_repo_url")
    @classmethod
    def normalize_repo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_git_repo_url(value)


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = None
    image: Optional[str] = None
    repo_url: Optional[str] = None  # deprecated, ignored
    branch: Optional[str] = None  # deprecated, ignored
    skill_repo_url: Optional[str] = None
    skill_branch: Optional[str] = None
    disk_size_gi: Optional[int] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None
    agent_type: Optional[str] = None
    capability_profile_id: Optional[str] = None
    policy_profile_id: Optional[str] = None
    runtime_profile_id: Optional[str] = None

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in ALLOWED_AGENT_TYPES:
            raise ValueError("agent_type must be one of: workspace, specialist, task")
        return normalized

    @field_validator("repo_url", "skill_repo_url")
    @classmethod
    def normalize_repo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_git_repo_url(value)


class AgentDeleteResponse(BaseModel):
    ok: bool
    destroy_data: bool


class AgentStatusResponse(BaseModel):
    id: str
    status: str
    cpu_usage: Optional[str] = None
    memory_usage: Optional[str] = None
    last_error: Optional[str] = None


class AgentChatModelProfileResponse(BaseModel):
    runtime_profile_id: Optional[str] = None
    revision: Optional[int] = None
    provider: str = ""
    current_model: str = ""


class AgentResponse(BaseModel):
    id: str
    name: str
    status: str
    visibility: str
    image: str
    repo_url: Optional[str] = None
    branch: Optional[str] = None
    skill_repo_url: Optional[str] = None
    skill_branch: Optional[str] = None
    owner_user_id: int
    cpu: Optional[str] = None
    memory: Optional[str] = None
    agent_type: str
    capability_profile_id: Optional[str] = None
    policy_profile_id: Optional[str] = None
    runtime_profile_id: Optional[str] = None
    disk_size_gi: int
    description: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("repo_url", "skill_repo_url", mode="before")
    @classmethod
    def normalize_repo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_git_repo_url(value)

    class Config:
        from_attributes = True
