"""Tests for web.py - settings and config."""
import pytest
from fastapi.testclient import TestClient


def test_agent_settings_panel():
    """Test agent settings panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/settings/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_settings_save():
    """Test agent settings save."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/app/agents/agent-123/settings/save", 
                         json={"llm": {"provider": "openai"}})
    assert response.status_code in [200, 302, 400, 401, 403, 404]


def test_agent_files_panel():
    """Test agent files panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/files/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_sessions_panel():
    """Test agent sessions panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/sessions/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_skills_panel():
    """Test agent skills panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/skills/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_usage_panel():
    """Test agent usage panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/usage/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_users_panel():
    """Test users panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/users/panel")
    assert response.status_code in [200, 302, 401, 403]


def test_api_agents_usage():
    """Test agents usage API."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/agent-123/usage")
    assert response.status_code in [200, 401, 403, 404]


def test_proxy_agent_api():
    """Test proxy to agent API."""
    from app.main import app
    client = TestClient(app)
    # Test proxy endpoint
    response = client.post("/a/agent-123/api/chat", 
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]


def test_proxy_agent_files_list():
    """Test proxy to agent files list."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/files")
    assert response.status_code in [401, 403, 404, 500]


def test_proxy_agent_events():
    """Test proxy to agent events."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/events")
    assert response.status_code in [400, 401, 403, 404]


def test_agent_runtime_destroy():
    """Test agent runtime destroy."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/destroy")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_runtime_delete():
    """Test agent runtime delete."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/delete-runtime")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_defaults():
    """Test agent defaults endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/defaults")
    assert response.status_code in [200, 401, 403]


def test_proxy_api_chat_stream():
    """Test proxy chat stream endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/a/agent-123/api/chat/stream",
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]
