from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Engineering Flow Platform Portal"
    base_uri: str = ""
    debug: bool = True
    database_url: str = "sqlite:///./portal.db"
    secret_key: str = "change-me-in-production"
    session_cookie_name: str = "portal_session"
    portal_internal_base_url: str = Field(default="", validation_alias="PORTAL_INTERNAL_BASE_URL")
    github_webhook_secret: str = Field(default="", validation_alias="GITHUB_WEBHOOK_SECRET")
    jira_webhook_shared_secret: str = Field(default="", validation_alias="JIRA_WEBHOOK_SHARED_SECRET")
    allow_insecure_provider_webhooks: bool = Field(default=False, validation_alias="ALLOW_INSECURE_PROVIDER_WEBHOOKS")
    # Preferred runtime-catalog alignment hook for Portal capability validation/routing.
    # If this snapshot is missing/invalid, Portal falls back to deterministic local seed mappings.
    runtime_capability_catalog_snapshot_json: str = Field(
        default="",
        validation_alias="RUNTIME_CAPABILITY_CATALOG_SNAPSHOT_JSON",
    )

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = Field(default="", validation_alias="BOOTSTRAP_ADMIN_PASSWORD")

    agents_namespace: str = "efp-agents"
    agents_volume_sub_path_prefix: str = "efp-agents"
    k8s_enabled: bool = True
    k8s_storage_class: str = "efp-agents-storage"
    k8s_pvc_size: str = "20Gi"
    k8s_pvc_access_modes: list = ["ReadWriteMany"]
    k8s_incluster: bool = True
    k8s_kubeconfig: Optional[str] = "/etc/rancher/k3s/k3s.yaml"
    k8s_agent_service_type: str = "ClusterIP"
    # GitHub HTTPS clone uses token-only auth. Username is fixed to x-access-token in askpass.
    k8s_git_token_key: Optional[str] = "GIT_TOKEN"

    # Default agent config (image repo without tag)
    default_agent_image_repo: str = "ghcr.io/dvnuo/engineering-flow-platform"
    default_agent_image_tag: str = "latest"
    default_runtime_type: str = Field(default="native", validation_alias="DEFAULT_RUNTIME_TYPE")
    default_native_runtime_image_repo: str = Field(default="", validation_alias="DEFAULT_NATIVE_RUNTIME_IMAGE_REPO")
    default_native_runtime_image_tag: str = Field(default="", validation_alias="DEFAULT_NATIVE_RUNTIME_IMAGE_TAG")
    default_opencode_runtime_image_repo: str = Field(
        default="ghcr.io/dvnuo/efp-opencode-runtime",
        validation_alias="DEFAULT_OPENCODE_RUNTIME_IMAGE_REPO",
    )
    default_opencode_runtime_image_tag: str = Field(default="1.14.29", validation_alias="DEFAULT_OPENCODE_RUNTIME_IMAGE_TAG")
    default_agent_git_image: str = "alpine/git:latest"
    default_agent_repo_url: str = "https://github.com/dvnuo/engineering-flow-platform"
    default_agent_branch: str = "master"
    default_agent_runtime_repo_url: str = Field(
        default="",
        validation_alias="DEFAULT_AGENT_RUNTIME_REPO_URL",
    )
    enable_runtime_source_overlay: bool = Field(default=False, validation_alias="ENABLE_RUNTIME_SOURCE_OVERLAY")
    default_agent_runtime_branch: str = Field(
        default="",
        validation_alias="DEFAULT_AGENT_RUNTIME_BRANCH",
    )
    default_skill_repo_url: str = Field(
        default="https://github.com/dvnuo/engineering-flow-platform-skills",
        validation_alias="DEFAULT_SKILL_REPO_URL",
    )
    default_skill_branch: str = Field(
        default="master",
        validation_alias="DEFAULT_SKILL_BRANCH",
    )
    default_tool_repo_url: str = Field(default="", validation_alias="DEFAULT_TOOL_REPO_URL")
    default_tool_branch: str = Field(default="main", validation_alias="DEFAULT_TOOL_BRANCH")
    default_native_tools_strict_mode: bool = Field(default=False, validation_alias="DEFAULT_NATIVE_TOOLS_STRICT_MODE")
    default_agent_disk_size_gi: int = 20
    default_agent_cpu: str = "250m"
    default_agent_memory: str = "512Mi"
    default_agent_mount_path: str = "/root/.efp"

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
    automation_rules_worker_enabled: bool = Field(default=True, validation_alias="AUTOMATION_RULES_WORKER_ENABLED")
    automation_rules_worker_interval_seconds: int = Field(default=15, validation_alias="AUTOMATION_RULES_WORKER_INTERVAL_SECONDS")
    automation_rule_lock_lease_seconds: int = Field(default=120, validation_alias="AUTOMATION_RULE_LOCK_LEASE_SECONDS")
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
