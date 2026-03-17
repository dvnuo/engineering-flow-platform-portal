"""Tests for app config module."""
import os
import pytest
from app.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Clear relevant environment variables before each test."""
    for key in list(os.environ.keys()):
        if key.startswith(('SECRET_', 'DATABASE_', 'DEBUG', 'AGENTS_', 'K8S_', 'GITHUB_', 'JIRA_', 'CONFLUENCE_')):
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
