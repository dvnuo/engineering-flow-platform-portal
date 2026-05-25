"""Tests for app config module."""
import os
import pytest
from app.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Clear relevant environment variables before each test."""
    # Clear all env vars that could affect Settings defaults
    env_prefixes = (
        'SECRET_', 'DATABASE_', 'DEBUG', 'AGENTS_', 'K8S_', 
        'GITHUB_', 'JIRA_', 'CONFLUENCE_', 'BOOTSTRAP_',
        'DEFAULT_', 'PORTAL_', 'RUNTIME_', 'ALLOW_INSECURE_', 'DELEGATION_', 'ASSETS_',
        'AGENT_TASK_', 'OPENCODE_'
    )
    for key in list(os.environ.keys()):
        if any(key.startswith(p) for p in env_prefixes):
            monkeypatch.delenv(key, raising=False)


def test_settings_defaults():
    """Test default settings values."""
    settings = Settings()
    assert settings.app_name == "Engineering Flow Platform Portal"
    assert settings.debug is False
    assert settings.database_url == "sqlite:///./portal.db"
    assert settings.secret_key == "change-me-in-production"
    assert settings.agents_namespace == "efp-agents"
    assert settings.k8s_enabled is False
    assert settings.k8s_storage_class == "local-path"


def test_settings_bootstrap_defaults():
    """Test bootstrap admin defaults."""
    settings = Settings()
    assert settings.bootstrap_admin_username == "admin"
    assert settings.bootstrap_admin_password == ""


def test_settings_k8s_defaults():
    """Test K8s defaults."""
    settings = Settings()
    assert settings.k8s_pvc_size == "20Gi"
    assert settings.k8s_pvc_access_modes == ["ReadWriteOnce"]
    assert settings.k8s_incluster is True
    assert settings.k8s_agent_service_type == "ClusterIP"


def test_settings_default_opencode_permission_env_defaults():
    settings = Settings()
    assert settings.default_opencode_permission_mode == "workspace_full_access"
    assert settings.default_opencode_allow_bash_all is True


def test_settings_default_opencode_permission_env_overrides(monkeypatch):
    monkeypatch.setenv("DEFAULT_OPENCODE_PERMISSION_MODE", "profile_policy")
    monkeypatch.setenv("DEFAULT_OPENCODE_ALLOW_BASH_ALL", "false")
    settings = Settings()
    assert settings.default_opencode_permission_mode == "profile_policy"
    assert settings.default_opencode_allow_bash_all is False


def test_settings_default_skill_asset_env_defaults():
    settings = Settings()
    assert settings.default_skill_repo_subdir == ""
    assert settings.default_skill_asset_version == ""


def test_settings_default_skill_asset_env_overrides(monkeypatch):
    monkeypatch.setenv("DEFAULT_SKILL_REPO_SUBDIR", "skills")
    monkeypatch.setenv("DEFAULT_SKILL_ASSET_VERSION", "sha-abc123")
    settings = Settings()
    assert settings.default_skill_repo_subdir == "skills"
    assert settings.default_skill_asset_version == "sha-abc123"


def test_settings_agent_task_timeout_defaults():
    settings = Settings()
    assert settings.agent_task_runtime_poll_timeout_seconds == 3600
    assert settings.agent_task_runtime_poll_interval_seconds == 1
    assert settings.opencode_task_completion_timeout_seconds == 3600
    assert settings.opencode_chat_submit_timeout_seconds == 900


def test_settings_agent_task_timeout_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_TASK_RUNTIME_POLL_TIMEOUT_SECONDS", "3700")
    monkeypatch.setenv("AGENT_TASK_RUNTIME_POLL_INTERVAL_SECONDS", "3")
    monkeypatch.setenv("OPENCODE_TASK_COMPLETION_TIMEOUT_SECONDS", "3800")
    monkeypatch.setenv("OPENCODE_CHAT_SUBMIT_TIMEOUT_SECONDS", "920")
    settings = Settings()
    assert settings.agent_task_runtime_poll_timeout_seconds == 3700
    assert settings.agent_task_runtime_poll_interval_seconds == 3
    assert settings.opencode_task_completion_timeout_seconds == 3800
    assert settings.opencode_chat_submit_timeout_seconds == 920
