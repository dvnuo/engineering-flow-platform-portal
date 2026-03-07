from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "Engineering Flow Platform Portal"
    debug: bool = False
    database_url: str = "sqlite:///./portal.db"
    secret_key: str = "change-me-in-production"
    session_cookie_name: str = "portal_session"

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin123"

    robots_namespace: str = "robots"
    k8s_enabled: bool = False
    k8s_storage_class: str = "gp3"
    k8s_incluster: bool = True
    k8s_kubeconfig: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
