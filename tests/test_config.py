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


def test_settings_default_skill_asset_env_defaults():
    settings = Settings()
    assert settings.default_skill_repo_subdir == ""
    assert settings.default_skill_asset_version == ""


def test_settings_exposes_runtime_selection_without_source_overlay(monkeypatch):
    monkeypatch.setenv("DEFAULT_RUNTIME_TYPE", "opencode")
    monkeypatch.setenv("ENABLE_RUNTIME_SOURCE_OVERLAY", "true")
    monkeypatch.setenv("DEFAULT_AGENT_RUNTIME_REPO_URL", "https://example.com/runtime.git")
    monkeypatch.setenv("DEFAULT_AGENT_RUNTIME_BRANCH", "main")
    settings = Settings()
    assert settings.default_runtime_type == "opencode"
    assert settings.default_opencode_runtime_image_repo == "ghcr.io/dvnuo/efp-opencode-runtime"
    assert settings.default_opencode_runtime_image_tag == "1.14.39"
    assert settings.default_opencode_permission_mode == "workspace_full_access"
    assert settings.default_opencode_allow_bash_all is True
    assert settings.opencode_workspace_repos_dir == "/workspace/repos"
    assert settings.opencode_git_checkout_timeout_seconds == 120
    assert settings.opencode_task_completion_timeout_seconds == 3600
    assert settings.opencode_chat_submit_timeout_seconds == 900
    assert not hasattr(settings, "enable_runtime_source_overlay")
    assert not hasattr(settings, "default_agent_runtime_repo_url")
    assert not hasattr(settings, "default_agent_runtime_branch")


def test_settings_opencode_runtime_env_overrides(monkeypatch):
    monkeypatch.setenv("DEFAULT_OPENCODE_RUNTIME_IMAGE_REPO", "ghcr.io/acme/opencode")
    monkeypatch.setenv("DEFAULT_OPENCODE_RUNTIME_IMAGE_TAG", "2.0.0")
    monkeypatch.setenv("DEFAULT_OPENCODE_PERMISSION_MODE", "ask")
    monkeypatch.setenv("DEFAULT_OPENCODE_ALLOW_BASH_ALL", "false")
    monkeypatch.setenv("OPENCODE_WORKSPACE_REPOS_DIR", "/workspace/custom-repos")
    monkeypatch.setenv("OPENCODE_GIT_CHECKOUT_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("OPENCODE_TASK_COMPLETION_TIMEOUT_SECONDS", "7200")
    monkeypatch.setenv("OPENCODE_CHAT_SUBMIT_TIMEOUT_SECONDS", "1200")

    settings = Settings()

    assert settings.default_opencode_runtime_image_repo == "ghcr.io/acme/opencode"
    assert settings.default_opencode_runtime_image_tag == "2.0.0"
    assert settings.default_opencode_permission_mode == "ask"
    assert settings.default_opencode_allow_bash_all is False
    assert settings.opencode_workspace_repos_dir == "/workspace/custom-repos"
    assert settings.opencode_git_checkout_timeout_seconds == 240
    assert settings.opencode_task_completion_timeout_seconds == 7200
    assert settings.opencode_chat_submit_timeout_seconds == 1200


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
    assert settings.agent_task_reconcile_worker_enabled is True
    assert settings.agent_task_reconcile_worker_interval_seconds == 5
    assert settings.agent_task_reconcile_worker_batch_size == 50
    assert settings.agent_task_runtime_missing_stale_after_seconds == 300


def test_settings_agent_task_timeout_env_overrides(monkeypatch):
    monkeypatch.setenv("AGENT_TASK_RUNTIME_POLL_TIMEOUT_SECONDS", "3700")
    monkeypatch.setenv("AGENT_TASK_RUNTIME_POLL_INTERVAL_SECONDS", "3")
    monkeypatch.setenv("AGENT_TASK_RECONCILE_WORKER_ENABLED", "false")
    monkeypatch.setenv("AGENT_TASK_RECONCILE_WORKER_INTERVAL_SECONDS", "9")
    monkeypatch.setenv("AGENT_TASK_RECONCILE_WORKER_BATCH_SIZE", "17")
    monkeypatch.setenv("AGENT_TASK_RUNTIME_MISSING_STALE_AFTER_SECONDS", "42")
    settings = Settings()
    assert settings.agent_task_runtime_poll_timeout_seconds == 3700
    assert settings.agent_task_runtime_poll_interval_seconds == 3
    assert settings.agent_task_reconcile_worker_enabled is False
    assert settings.agent_task_reconcile_worker_interval_seconds == 9
    assert settings.agent_task_reconcile_worker_batch_size == 17
    assert settings.agent_task_runtime_missing_stale_after_seconds == 42
