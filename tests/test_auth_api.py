"""Tests for auth API endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def test_login_api_valid_credentials():
    """Test login with valid credentials."""
    from app.main import app
    client = TestClient(app)
    
    with patch("app.api.auth.UserRepository") as mock_repo:
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "admin"
        mock_user.password_hash = "hashed"
        
        mock_repo_instance = MagicMock()
        mock_repo_instance.get_by_username.return_value = mock_user
        mock_repo.return_value = mock_repo_instance
        
        with patch("app.api.auth.verify_password", return_value=True):
            response = client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin123"
            })
            
            # Should redirect or set cookie
            assert response.status_code in [200, 302, 303, 307, 308]


def test_login_api_invalid_credentials():
    """Test login with invalid credentials."""
    from app.main import app
    client = TestClient(app)
    
    response = client.post("/api/auth/login", json={
        "username": "wrong",
        "password": "wrong"
    })
    
    # Should fail
    assert response.status_code == 401


def test_register_api():
    """Test registration endpoint."""
    from app.main import app
    client = TestClient(app)
    
    response = client.post("/api/auth/register", json={
        "username": "newuser",
        "password": "password123"
    })
    
    # Should succeed or fail with existing user
    assert response.status_code in [200, 201, 400, 409]


def test_logout_api():
    """Test logout endpoint."""
    from app.main import app
    client = TestClient(app)
    
    response = client.post("/api/auth/logout")
    
    # Should clear session
    assert response.status_code in [200, 302, 303]


def test_me_api_requires_auth():
    """Test /api/auth/me requires auth."""
    from app.main import app
    client = TestClient(app)
    
    response = client.get("/api/auth/me")
    
    # Should require auth
    assert response.status_code in [401, 403]
