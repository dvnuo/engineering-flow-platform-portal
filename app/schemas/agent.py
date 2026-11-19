from datetime import datetime

from pydantic import BaseModel, Field, field_validator
from typing import Optional

from app.contracts.runtime_type import ALLOWED_RUNTIME_TYPES, normalize_runtime_type, normalize_runtime_type_or_default
from app.utils.git_urls import normalize_git_repo_url

ALLOWED_AGENT_TYPES = {"workspace", "specialist", "task"}


class AgentCreateRequest(BaseModel):
    model_config = {"extra": "ignore"}
    name: str
    image: Optional[str] = None
    runtime_type: str = "native"
    repo_url: Optional[str] = None  # deprecated, ignored
    branch: Optional[str] = None  # deprecated, ignored
    agent_settings_repo_url: Optional[str] = None
    agent_settings_branch: Optional[str] = None
    agent_settings_subdir: Optional[str] = None
    skill_repo_url: Optional[str] = None
    skill_branch: Optional[str] = None
    disk_size_gi: int = 20
    mount_path: str = "/workspace"
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None
    agent_type: str = "workspace"

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ALLOWED_AGENT_TYPES:
            raise ValueError("agent_type must be one of: workspace, specialist, task")
        return normalized

    @field_validator("repo_url", "agent_settings_repo_url", "skill_repo_url")
    @classmethod
    def normalize_repo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_git_repo_url(value)

    @field_validator("agent_settings_branch", "agent_settings_subdir", "skill_branch")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("runtime_type")
    @classmethod
    def validate_runtime_type(cls, value: Optional[str]) -> str:
        if value is None:
            return "native"
        return normalize_runtime_type(value)


class AgentUpdateRequest(BaseModel):
    model_config = {"extra": "ignore"}
    name: Optional[str] = None
    image: Optional[str] = None
    runtime_type: Optional[str] = None
    repo_url: Optional[str] = None  # deprecated, ignored
    branch: Optional[str] = None  # deprecated, ignored
    agent_settings_repo_url: Optional[str] = None
    agent_settings_branch: Optional[str] = None
    agent_settings_subdir: Optional[str] = None
    skill_repo_url: Optional[str] = None
    skill_branch: Optional[str] = None
    disk_size_gi: Optional[int] = None
    cpu: Optional[str] = None
    memory: Optional[str] = None
    description: Optional[str] = None
    agent_type: Optional[str] = None

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in ALLOWED_AGENT_TYPES:
            raise ValueError("agent_type must be one of: workspace, specialist, task")
        return normalized

    @field_validator("repo_url", "agent_settings_repo_url", "skill_repo_url")
    @classmethod
    def normalize_repo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_git_repo_url(value)

    @field_validator("agent_settings_branch", "agent_settings_subdir", "skill_branch")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("runtime_type")
    @classmethod
    def validate_runtime_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_runtime_type(value)


class AgentDeleteResponse(BaseModel):
    ok: bool


class AgentStatusResponse(BaseModel):
    id: str
    status: str
    cpu_usage: Optional[str] = None
    memory_usage: Optional[str] = None
    last_error: Optional[str] = None
    desired_profile_revision: Optional[int] = None
    applied_profile_revision: Optional[int] = None
    # Running with connector settings older than the member's current ones; a
    # restart applies them (the Portal skipped it because the assistant was busy).
    settings_restart_pending: bool = False
    # Member-facing reading of the same state: what phase startup is in, and
    # what to do when it fails. Raw `status`/`last_error` stay untouched so
    # existing consumers are unaffected.
    startup: Optional[dict] = None


class AgentResourceAmounts(BaseModel):
    """One set of Kubernetes quantities, as written on the pod (``250m``, ``2Gi``)."""

    cpu: Optional[str] = None
    memory: Optional[str] = None
    ephemeral_storage: Optional[str] = None


class AgentResourceUsage(BaseModel):
    cpu: Optional[str] = None
    memory: Optional[str] = None
    cpu_millicores: Optional[int] = None
    memory_bytes: Optional[int] = None
    cpu_percent_of_limit: Optional[int] = None
    memory_percent_of_limit: Optional[int] = None


class AgentContainerTermination(BaseModel):
    """Why the assistant's container last stopped (``OOMKilled``, ``Error``, ...)."""

    reason: Optional[str] = None
    exit_code: Optional[int] = None
    finished_at: Optional[str] = None


class AgentPodEviction(BaseModel):
    reason: str = "Evicted"
    message: Optional[str] = None
    at: Optional[str] = None


class AgentResourcesResponse(BaseModel):
    """What an assistant's pod is allowed, what it is using, and why it last stopped.

    ``source`` is ``pod`` when the amounts were read from the running pod and
    ``configured`` when they are what the Portal would put on one (stopped
    assistant, or no cluster). ``usage`` needs the cluster's metrics API;
    ``usage_status`` says why it is missing: ``kubernetes_disabled``, ``no_pod``,
    ``metrics_unavailable``, ``metrics_forbidden`` or ``error``.
    """

    id: str
    source: str = "configured"
    requests: AgentResourceAmounts = Field(default_factory=AgentResourceAmounts)
    limits: AgentResourceAmounts = Field(default_factory=AgentResourceAmounts)
    usage: Optional[AgentResourceUsage] = None
    usage_status: str = "kubernetes_disabled"
    pod_name: Optional[str] = None
    pod_started_at: Optional[str] = None
    restart_count: int = 0
    last_termination: Optional[AgentContainerTermination] = None
    last_eviction: Optional[AgentPodEviction] = None


class AgentStatusBatchResponse(BaseModel):
    """Status of every assistant the caller can see, in one round trip."""

    statuses: list[AgentStatusResponse]


class AgentChatModelProfileResponse(BaseModel):
    runtime_profile_id: Optional[str] = None
    revision: Optional[int] = None
    runtime_type: str = "native"
    provider: str = ""
    current_model: str = ""
    current_reasoning_effort: str = "medium"
    current_max_context_tokens: Optional[int] = None
    available_models: list[str] = Field(default_factory=list)
    reasoning_efforts: list[str] = Field(default_factory=list)
    context_sizes: list[int] = Field(default_factory=list)
    supports_reasoning_effort: bool = False
    supports_context_size: bool = False
    # Labels for ids the static picker table does not know (the Portal-wide
    # catalog's names), where the list came from and when.
    model_labels: dict[str, str] = Field(default_factory=dict)
    # Model id -> context window (tokens) from the catalog; the context-size
    # presets follow it per model (contextSizeOptionsForModel in chat_ui.js).
    model_context_windows: dict[str, int] = Field(default_factory=dict)
    # Model id -> the reasoning efforts the catalog lists; the thinking-level
    # options follow it per model (reasoningEffortOptionsForModel).
    model_reasoning_efforts: dict[str, list[str]] = Field(default_factory=dict)
    models_source: str = "static"
    models_fetched_at: Optional[str] = None


class SessionRunSettingsRequest(BaseModel):
    """Run settings for one chat session; None (or empty) clears that setting."""

    model_override: Optional[str] = None
    reasoning_effort: Optional[str] = None
    max_context_tokens: Optional[int] = None


class SessionRunSettingsResponse(BaseModel):
    agent_id: str
    session_id: str
    model_override: Optional[str] = None
    reasoning_effort: Optional[str] = None
    max_context_tokens: Optional[int] = None


class AgentResponse(BaseModel):
    id: str
    name: str
    status: str
    visibility: str
    image: str
    runtime_type: str = "native"
    repo_url: Optional[str] = None
    branch: Optional[str] = None
    agent_settings_repo_url: Optional[str] = None
    agent_settings_branch: Optional[str] = None
    agent_settings_subdir: Optional[str] = None
    effective_agent_settings_repo_url: Optional[str] = None
    effective_agent_settings_branch: Optional[str] = None
    effective_agent_settings_subdir: Optional[str] = None
    skill_repo_url: Optional[str] = None
    skill_branch: Optional[str] = None
    effective_skill_repo_url: Optional[str] = None
    effective_skill_branch: Optional[str] = None
    owner_user_id: int
    cpu: Optional[str] = None
    memory: Optional[str] = None
    agent_type: str
    runtime_profile_id: Optional[str] = None
    disk_size_gi: int
    description: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_validator("repo_url", "agent_settings_repo_url", "skill_repo_url", mode="before")
    @classmethod
    def normalize_repo_url(cls, value: Optional[str]) -> Optional[str]:
        return normalize_git_repo_url(value)
    @field_validator("runtime_type", mode="before")
    @classmethod
    def validate_response_runtime_type(cls, value: Optional[str]) -> str:
        return normalize_runtime_type_or_default(value)

    class Config:
        from_attributes = True
