from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Engineering Flow Platform Portal"
    debug: bool = False
    database_url: str = "sqlite:///./portal.db"
    secret_key: str = "change-me-in-production"
    session_cookie_name: str = "portal_session"

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = Field(default="", validation_alias="BOOTSTRAP_ADMIN_PASSWORD")

    agents_namespace: str = "efp-agents"
    k8s_enabled: bool = False
    k8s_storage_class: str = "local-path"
    k8s_pvc_size: str = "20Gi"
    k8s_pvc_access_modes: list = ["ReadWriteOnce"]
    k8s_incluster: bool = True
    k8s_kubeconfig: Optional[str] = "/etc/rancher/k3s/k3s.yaml"
    k8s_agent_service_type: str = "ClusterIP"
    k8s_git_username_key: Optional[str] = None
    k8s_git_token_key: Optional[str] = None

    # Default agent config (image repo without tag)
    default_agent_image_repo: str = "ghcr.io/dvnuo/engineering-flow-platform"
    default_agent_image_tag: str = "latest"
    default_agent_git_image: str = "alpine/git:latest"
    default_agent_repo_url: str = "https://github.com/dvnuo/engineering-flow-platform"
    default_agent_branch: str = "master"
    default_agent_disk_size_gi: int = 20
    default_agent_cpu: str = "250m"
    default_agent_memory: str = "512Mi"
    default_agent_mount_path: str = "/root/.efp"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
