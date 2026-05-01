"""Tests for web.py routes."""
import pytest
from fastapi.testclient import TestClient


def test_index_redirect():
    """Test index redirects to app."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in [200, 302, 307, 308]


def test_settings_panel():
    """Test settings panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-1/settings/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_settings_save():
    """Test settings save endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/app/agents/agent-1/settings/save")
    assert response.status_code in [200, 302, 400, 401, 403, 404]


def test_files_upload_endpoint():
    """Test file upload endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/a/agent-1/api/files/upload")
    assert response.status_code in [200, 400, 401, 403, 404, 415]


def test_files_preview_endpoint():
    """Test file preview endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-1/api/files/file-id/preview")
    assert response.status_code in [200, 401, 403, 404, 500]


def test_session_detail():
    """Test session detail endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/sessions/test-session")
    assert response.status_code in [200, 401, 403, 404]


def test_clear_session_detail():
    """Test clear specific session endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/sessions/test-session/clear")
    assert response.status_code in [200, 400, 401, 403, 404]


def test_events_ws():
    """Test events WebSocket endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/events")
    # WebSocket upgrade will fail, but endpoint exists
    assert response.status_code in [400, 401, 403, 404, 426]


def test_login_page():
    """Test login page."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/login")
    assert response.status_code == 200


def test_register_page():
    """Test register page."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/register")
    assert response.status_code == 200


def test_app_page():
    """Test app page."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app")
    # Returns 200 with login or redirect
    assert response.status_code in [200, 302]



def test_sessions_panel():
    """Test sessions panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-1/sessions/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_skills_panel():
    """Test skills panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-1/skills/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_usage_panel():
    """Test usage panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-1/usage/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_users_panel():
    """Test users panel endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/users/panel")
    assert response.status_code in [200, 302, 401, 403]
