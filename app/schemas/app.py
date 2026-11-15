"""Request and response shapes of the Apps owner API (``/api/apps``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# --- requests --------------------------------------------------------------------


class AppCreateRequest(BaseModel):
    repo_url: str
    ref: str
    entry_dir: str = ""
    # Empty = the name in efp-app.json.
    name: str = ""
    # Empty = made from the name.
    slug: Optional[str] = None
    # None = the description in efp-app.json.
    description: Optional[str] = None
    # None = private.
    visibility: Optional[str] = None
    # Start the first deployment right after registering.
    deploy: bool = True


class AppInspectRequest(BaseModel):
    repo_url: str
    ref: str
    entry_dir: str = ""


class AppUpdateRequest(BaseModel):
    """Only the fields that are sent are changed; ``description: null`` clears it."""

    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None
    env: Optional[dict[str, Any]] = None
    repo_ref: Optional[str] = None
    llm_enabled: Optional[bool] = None
    connectors_enabled: Optional[list[str]] = None
    connectors_write: Optional[list[str]] = None
    # System administrators only; null = the platform default.
    daily_credits: Optional[float] = None
    daily_tokens: Optional[int] = None


class AppDeployRequest(BaseModel):
    # None = the branch or tag the app follows.
    ref: Optional[str] = None


class AppDisableRequest(BaseModel):
    reason: str


class AppAnyoneApprovalRequest(BaseModel):
    approved: bool


class AppTransferRequest(BaseModel):
    username: str


# --- responses -------------------------------------------------------------------


class AppMember(BaseModel):
    id: int
    username: str
    display_name: str
    group_id: Optional[int] = None
    group_name: str = ""


class AppReleaseSummary(BaseModel):
    id: str
    kind: str
    status: str
    commit_sha: Optional[str] = None
    ref: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None


class AppManageFields(BaseModel):
    """What only the owner and system administrators see."""

    repo_url: str
    repo_ref: str
    entry_dir: str
    env: dict[str, str]
    llm_enabled: bool
    connectors_enabled: list[str]
    connectors_write: list[str]
    daily_credits: Optional[float] = None
    daily_tokens: Optional[int] = None
    default_daily_credits: float
    default_daily_tokens: int
    token_last_used_at: Optional[datetime] = None
    token_last_used_source: Optional[str] = None
    anyone_approved_by: Optional[int] = None
    # The deployment that is queued or running, if any.
    active_release: Optional[AppReleaseSummary] = None
    # The most recent deployment or rollback, whatever its outcome.
    latest_release: Optional[AppReleaseSummary] = None


class AppResponse(BaseModel):
    id: str
    slug: str
    name: str
    description: Optional[str] = None
    # {APPS_PUBLIC_BASE_URL}/{slug}/; "" when the public address is not configured.
    url: str
    type: Optional[str] = None
    visibility: str
    # What is in force: "anyone" only when the platform allows it and the app is approved.
    effective_visibility: str
    anyone_pending_approval: bool
    owner: AppMember
    is_owner: bool
    can_manage: bool
    enabled: bool
    # An administrator's reason, or "owner_inactive".
    disabled_reason: Optional[str] = None
    desired_state: str
    is_template: bool
    template_app_id: Optional[str] = None
    template_version_id: Optional[str] = None
    has_icon: bool
    icon_url: Optional[str] = None
    deployed: bool
    current_release: Optional[AppReleaseSummary] = None
    # The host's last known state of the app (docs/CONTRACTS.md, section 4.6); None when unknown.
    state: Optional[str] = None
    state_reason: Optional[str] = None
    # When the signed-in member last used the app.
    last_visited_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    manage: Optional[AppManageFields] = None


class AppListResponse(BaseModel):
    scope: str
    apps: list[AppResponse]
    # Whether the states above were just read from the host (false: the last report, possibly none).
    host_reachable: bool


class AppCreateResponse(BaseModel):
    app: AppResponse
    # The first deployment, when one was started.
    release: Optional["AppReleaseResponse"] = None
    # Why the first deployment could not be started (the app exists all the same).
    deploy_error: Optional[dict[str, Any]] = None


class AppReleaseResponse(BaseModel):
    id: str
    app_id: str
    kind: str
    status: str
    commit_sha: Optional[str] = None
    ref: Optional[str] = None
    commit_message: Optional[str] = None
    requested_by: Optional[AppMember] = None
    # [{"source": "portal"|"host", "name", "status", "started_at", "finished_at", "note"?, "note_key"?, "note_params"?, "error"?}]
    steps: list[dict[str, Any]]
    error_summary: Optional[str] = None
    host_job_id: Optional[str] = None
    log_tail: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    is_current: bool = False


class AppReleaseListResponse(BaseModel):
    releases: list[AppReleaseResponse]
    current_release_id: Optional[str] = None
    # The release a rollback would return to.
    rollback_release_id: Optional[str] = None


class AppReleaseLogResponse(BaseModel):
    text: str
    offset: int
    size: int


class AppStatusResponse(BaseModel):
    host_reachable: bool
    # True: read from the host just now. False: the host's last report.
    live: bool
    state: Optional[str] = None
    reason: Optional[str] = None
    # The host's status object as it sent it; None when the host has no record of the app.
    status: Optional[dict[str, Any]] = None
    reported_at: Optional[datetime] = None
    error: Optional[dict[str, Any]] = None
    enabled: bool
    disabled_reason: Optional[str] = None
    desired_state: str
    deployed: bool
    current_release_id: Optional[str] = None
    current_sha: Optional[str] = None
    repo_ref: str
    # How many commits the followed branch or tag is ahead of what runs; None when unknown.
    commits_behind: Optional[int] = None
    deploying: bool
    active_release_id: Optional[str] = None
    rollback_available: bool
    url: str


class AppLogsResponse(BaseModel):
    lines: list[str]
    truncated: bool = False


class AppTokenResponse(BaseModel):
    token: str
    last_used_at: Optional[datetime] = None
    last_used_source: Optional[str] = None
    # Only after a reset: whether the host has the new token.
    host_synced: Optional[bool] = None
    host_error: Optional[dict[str, Any]] = None


class AppDataResponse(BaseModel):
    data_bytes: Optional[int] = None
    host_reachable: bool
    live: bool
    reported_at: Optional[datetime] = None


class AppEventResponse(BaseModel):
    id: int
    action: str
    actor: Optional[AppMember] = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AppEventListResponse(BaseModel):
    events: list[AppEventResponse]


class AppInspectResponse(BaseModel):
    repo: str
    repo_url: str
    ref: str
    commit_sha: str
    commit_message: str = ""
    entry_dir: str
    manifest: dict[str, Any]
    suggested_name: str
    suggested_slug: str


class AppRepoRefsResponse(BaseModel):
    repo: str
    default_branch: Optional[str] = None
    branches: list[str]
    tags: list[str]


class AppSettingsResponse(BaseModel):
    public_base_url: str
    anyone_enabled: bool
    # The host apps' repositories must be on, and the account that reads them (None when it cannot be told).
    github_host: str
    service_account: Optional[str] = None
    default_daily_credits: float
    default_daily_tokens: int
    connector_types: list[str]
    visibilities: list[str]
    app_types: list[str]
    reserved_slugs: list[str]


AppCreateResponse.model_rebuild()
