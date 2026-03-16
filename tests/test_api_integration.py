"""Tests for API endpoints using FastAPI TestClient."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def test_app_exists():
    """Test app can be imported."""
    from app.main import app
    assert app is not None


def test_app_client():
    """Test app can create test client."""
    from app.main import app
    client = TestClient(app)
    assert client is not None


def test_app_root_endpoint():
    """Test root endpoint."""
    from app.main import app
    client = TestClient(app)
    # Root should redirect or return something
    response = client.get("/")
    assert response.status_code in [200, 302, 307, 308]


def test_login_page():
    """Test login page endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/login")
    assert response.status_code == 200


def test_register_page():
    """Test register page endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/register")
    assert response.status_code == 200


def test_app_page_redirects_or_shows_login():
    """Test /app shows login for unauthenticated users."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app")
    # Should return 200 with login page
    assert response.status_code == 200
    assert "login" in response.text.lower() or "login" in response.url.path.lower()


def test_api_agents_mine_requires_auth():
    """Test /api/agents/mine requires auth."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/mine")
    # Should return 401 or 403
    assert response.status_code in [401, 403]


def test_api_agents_public_requires_auth():
    """Test /api/agents/public requires auth."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/public")
    # Should return 401 or 403
    assert response.status_code in [401, 403]


def test_api_agents_create_requires_auth():
    """Test POST /api/agents requires auth."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents", json={"name": "test"})
    # Should return 401 or 403
    assert response.status_code in [401, 403]
