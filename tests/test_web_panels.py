"""Tests for web panel endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def test_app_agents_panel():
    """Test /app/agents panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/test-agent/panel")
    # Should return 200 or redirect or 404
    assert response.status_code in [200, 302, 307, 401, 403, 404]


def test_app_sessions_panel():
    """Test /app/agents/{id}/sessions/panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/test-agent/sessions/panel")
    assert response.status_code in [200, 302, 307, 401, 403]


def test_app_skills_panel():
    """Test /app/agents/{id}/skills/panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/test-agent/skills/panel")
    assert response.status_code in [200, 302, 307, 401, 403]


def test_app_files_panel():
    """Test /app/agents/{id}/files/panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/test-agent/files/panel")
    assert response.status_code in [200, 302, 307, 401, 403]


def test_app_settings_panel():
    """Test /app/agents/{id}/settings/panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/test-agent/settings/panel")
    assert response.status_code in [200, 302, 307, 401, 403]


def test_app_usage_panel():
    """Test /app/agents/{id}/usage/panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/test-agent/usage/panel")
    assert response.status_code in [200, 302, 307, 401, 403]


def test_app_users_panel():
    """Test /app/users/panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/users/panel")
    assert response.status_code in [200, 302, 307, 401, 403]


def test_proxy_agent_usage():
    """Test canonical runtime usage proxy endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/test-agent/api/usage")
    assert response.status_code in [401, 403, 404, 409, 502]
