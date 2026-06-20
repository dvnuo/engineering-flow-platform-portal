from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Engineering Flow Platform Portal"
    base_uri: str = ""
    debug: bool = False
    database_url: str = "sqlite:///./portal.db"
    secret_key: str = "change-me-in-production"
    session_cookie_name: str = "portal_session"
    portal_internal_base_url: str = Field(default="", validation_alias="PORTAL_INTERNAL_BASE_URL")
    # Preferred runtime-catalog alignment hook for Portal runtime compatibility metadata.
    # If this snapshot is missing/invalid, Portal falls back to deterministic local seed mappings.
    runtime_capability_catalog_snapshot_json: str = Field(
        default="",
        validation_alias="RUNTIME_CAPABILITY_CATALOG_SNAPSHOT_JSON",
    )

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = Field(default="", validation_alias="BOOTSTRAP_ADMIN_PASSWORD")

    agents_namespace: str = "efp-agents"
    agents_volume_sub_path_prefix: str = "efp-agents"
    k8s_enabled: bool = False
    k8s_storage_class: str = "local-path"
    k8s_pvc_size: str = "20Gi"
    k8s_pvc_access_modes: list = ["ReadWriteOnce"]
    k8s_incluster: bool = True
    k8s_kubeconfig: Optional[str] = "/etc/rancher/k3s/k3s.yaml"
    k8s_agent_service_type: str = "ClusterIP"
    # GitHub HTTPS clone uses token-only auth. Username is fixed to x-access-token in askpass.
    k8s_git_token_key: Optional[str] = "GIT_TOKEN"
    git_repo_auth_username: str = Field(
        default="x-access-token",
        validation_alias=AliasChoices("GIT_REPO_AUTH_USERNAME", "GIT_USERNAME"),
    )
    git_repo_auth_pat: str = Field(
        default="",
        validation_alias=AliasChoices("GIT_REPO_AUTH_PAT", "GIT_PAT", "GIT_TOKEN"),
    )
    git_repo_ls_remote_timeout_seconds: int = Field(
        default=12,
        validation_alias="GIT_REPO_LS_REMOTE_TIMEOUT_SECONDS",
    )

    # Default agent config (image repo without tag)
    default_agent_image_repo: str = "ghcr.io/dvnuo/engineering-flow-platform"
    default_agent_image_tag: str = "latest"
    default_runtime_type: str = Field(default="native", validation_alias="DEFAULT_RUNTIME_TYPE")
    default_opencode_runtime_image_repo: str = Field(
        default="ghcr.io/dvnuo/efp-opencode-runtime",
        validation_alias="DEFAULT_OPENCODE_RUNTIME_IMAGE_REPO",
    )
    default_opencode_runtime_image_tag: str = Field(default="1.14.39", validation_alias="DEFAULT_OPENCODE_RUNTIME_IMAGE_TAG")
    default_agent_git_image: str = "alpine/git:latest"
    default_agent_settings_repo_url: str = Field(
        default="https://github.com/dvnuo/engineering-flow-platform-agents",
        validation_alias="DEFAULT_AGENT_SETTINGS_REPO_URL",
    )
    default_agent_settings_branch: str = Field(
        default="master",
        validation_alias="DEFAULT_AGENT_SETTINGS_BRANCH",
    )
    default_agent_settings_repo_subdir: str = Field(
        default="",
        validation_alias="DEFAULT_AGENT_SETTINGS_REPO_SUBDIR",
    )
    default_agent_settings_asset_version: str = Field(
        default="",
        validation_alias="DEFAULT_AGENT_SETTINGS_ASSET_VERSION",
    )
    default_skill_repo_url: str = Field(
        default="https://github.com/dvnuo/engineering-flow-platform-skills",
        validation_alias="DEFAULT_SKILL_REPO_URL",
    )
    default_skill_branch: str = Field(
        default="master",
        validation_alias="DEFAULT_SKILL_BRANCH",
    )
    default_skill_repo_subdir: str = Field(
        default="",
        validation_alias="DEFAULT_SKILL_REPO_SUBDIR",
    )
    default_skill_asset_version: str = Field(
        default="",
        validation_alias="DEFAULT_SKILL_ASSET_VERSION",
    )
    default_opencode_permission_mode: str = Field(
        default="workspace_full_access",
        validation_alias="DEFAULT_OPENCODE_PERMISSION_MODE",
    )
    default_opencode_allow_bash_all: bool = Field(
        default=True,
        validation_alias="DEFAULT_OPENCODE_ALLOW_BASH_ALL",
    )
    opencode_workspace_repos_dir: str = Field(
        default="/workspace/repos",
        validation_alias="OPENCODE_WORKSPACE_REPOS_DIR",
    )
    opencode_git_checkout_timeout_seconds: int = Field(
        default=120,
        validation_alias="OPENCODE_GIT_CHECKOUT_TIMEOUT_SECONDS",
    )
    opencode_task_completion_timeout_seconds: int = Field(
        default=3600,
        validation_alias="OPENCODE_TASK_COMPLETION_TIMEOUT_SECONDS",
    )
    opencode_chat_submit_timeout_seconds: int = Field(
        default=900,
        validation_alias="OPENCODE_CHAT_SUBMIT_TIMEOUT_SECONDS",
    )
    default_agent_disk_size_gi: int = 20
    default_agent_cpu: str = "250m"
    default_agent_memory: str = "512Mi"
    default_agent_mount_path: str = "/workspace"

    assets_github_token: str = Field(default="", validation_alias="ASSETS_GITHUB_TOKEN")
    assets_github_api_base_url: str = Field(default="https://api.github.com", validation_alias="ASSETS_GITHUB_API_BASE_URL")
    assets_repo_full_name: str = Field(
        default="dvnuo/engineering-flow-platform-assets",
        validation_alias="ASSETS_REPO_FULL_NAME",
    )
    assets_default_base_branch: str = Field(default="main", validation_alias="ASSETS_DEFAULT_BASE_BRANCH")
    assets_bundle_root_dir: str = Field(default="requirement-bundles", validation_alias="ASSETS_BUNDLE_ROOT_DIR")
    assets_bundle_list_cache_ttl_seconds: int = Field(
        default=60,
        validation_alias="ASSETS_BUNDLE_LIST_CACHE_TTL_SECONDS",
    )
    delegation_rules_worker_enabled: bool = Field(default=True, validation_alias="DELEGATION_RULES_WORKER_ENABLED")
    delegation_rules_worker_interval_seconds: int = Field(default=15, validation_alias="DELEGATION_RULES_WORKER_INTERVAL_SECONDS")
    delegation_rule_lock_lease_seconds: int = Field(default=120, validation_alias="DELEGATION_RULE_LOCK_LEASE_SECONDS")
    agent_task_runtime_poll_timeout_seconds: int = Field(default=3600, validation_alias="AGENT_TASK_RUNTIME_POLL_TIMEOUT_SECONDS")
    agent_task_runtime_poll_interval_seconds: int = Field(default=1, validation_alias="AGENT_TASK_RUNTIME_POLL_INTERVAL_SECONDS")
    agent_task_reconcile_worker_enabled: bool = Field(default=True, validation_alias="AGENT_TASK_RECONCILE_WORKER_ENABLED")
    agent_task_reconcile_worker_initial_delay_seconds: int = Field(default=30, validation_alias="AGENT_TASK_RECONCILE_WORKER_INITIAL_DELAY_SECONDS")
    agent_task_reconcile_worker_interval_seconds: int = Field(default=5, validation_alias="AGENT_TASK_RECONCILE_WORKER_INTERVAL_SECONDS")
    agent_task_reconcile_worker_batch_size: int = Field(default=50, validation_alias="AGENT_TASK_RECONCILE_WORKER_BATCH_SIZE")
    agent_task_runtime_status_max_bytes: int = Field(default=2_000_000, validation_alias="AGENT_TASK_RUNTIME_STATUS_MAX_BYTES")
    agent_task_runtime_missing_stale_after_seconds: int = Field(default=300, validation_alias="AGENT_TASK_RUNTIME_MISSING_STALE_AFTER_SECONDS")
    runtime_profile_sync_worker_enabled: bool = Field(default=True, validation_alias="RUNTIME_PROFILE_SYNC_WORKER_ENABLED")
    runtime_profile_sync_worker_interval_seconds: int = Field(default=5, validation_alias="RUNTIME_PROFILE_SYNC_WORKER_INTERVAL_SECONDS")
    runtime_profile_sync_worker_batch_size: int = Field(default=20, validation_alias="RUNTIME_PROFILE_SYNC_WORKER_BATCH_SIZE")
    runtime_profile_sync_job_lock_lease_seconds: int = Field(default=60, validation_alias="RUNTIME_PROFILE_SYNC_JOB_LOCK_LEASE_SECONDS")
    runtime_profile_sync_push_timeout_seconds: int = Field(default=10, validation_alias="RUNTIME_PROFILE_SYNC_PUSH_TIMEOUT_SECONDS")
    runtime_profile_sync_job_max_attempts: int = Field(default=40, validation_alias="RUNTIME_PROFILE_SYNC_JOB_MAX_ATTEMPTS")
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
