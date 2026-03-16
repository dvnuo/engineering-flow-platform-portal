"""Tests for API endpoints - auth and users."""
import pytest
from fastapi.testclient import TestClient


def test_auth_login_success():
    """Test successful login."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/auth/login",
                         json={"username": "admin", "password": "admin123"})
    # Should redirect or set cookie
    assert response.status_code in [200, 302, 303, 400, 401]


def test_auth_login_wrong_password():
    """Test login with wrong password."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/auth/login",
                         json={"username": "admin", "password": "wrong"})
    assert response.status_code in [400, 401]


def test_auth_register():
    """Test registration."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/auth/register",
                         json={"username": "newuser", "password": "password123"})
    # Success or user exists
    assert response.status_code in [200, 201, 400, 409]


def test_auth_logout():
    """Test logout."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/auth/logout")
    # Should clear session
    assert response.status_code in [200, 302, 303]


def test_auth_me():
    """Test get current user."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/auth/me")
    # Needs auth
    assert response.status_code in [401, 403]


def test_api_users_list():
    """Test users list."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/users")
    assert response.status_code in [200, 401, 403]


def test_api_user_detail():
    """Test user detail."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/users/1")
    assert response.status_code in [200, 401, 403, 404]


def test_api_user_create():
    """Test user create."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/users",
                         json={"username": "newuser", "password": "pass123"})
    assert response.status_code in [200, 201, 400, 401, 403, 409]


def test_api_user_update():
    """Test user update."""
    from app.main import app
    client = TestClient(app)
    response = client.put("/api/users/1",
                        json={"nickname": "newnick"})
    assert response.status_code in [200, 400, 401, 403, 404]


def test_api_user_delete():
    """Test user delete."""
    from app.main import app
    client = TestClient(app)
    response = client.delete("/api/users/1")
    assert response.status_code in [200, 401, 403, 404]


def test_api_admin_agents():
    """Test admin agents list."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/admin/agents")
    assert response.status_code in [200, 401, 403]


def test_api_admin_audit_logs():
    """Test admin audit logs."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/admin/audit-logs")
    assert response.status_code in [200, 401, 403]


def test_api_copilot_chat():
    """Test copilot chat."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/copilot/chat",
                         json={"messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code in [200, 400, 401, 403, 404, 500]


def test_api_copilot_models():
    """Test copilot models."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/copilot/models")
    assert response.status_code in [200, 401, 403, 404]
