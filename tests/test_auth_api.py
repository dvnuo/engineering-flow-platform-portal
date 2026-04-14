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

    with patch("app.api.auth.UserRepository") as mock_repo:
        mock_repo_instance = MagicMock()
        mock_repo_instance.get_by_username.return_value = None
        mock_repo.return_value = mock_repo_instance

        response = client.post("/api/auth/login", json={"username": "wrong", "password": "wrong"})
        assert response.status_code == 401


def test_register_api():
    """Test registration endpoint."""
    from app.main import app
    client = TestClient(app)

    with patch("app.api.auth.UserRepository") as mock_repo:
        mock_repo_instance = MagicMock()
        mock_repo_instance.get_by_username.return_value = None
        created = MagicMock()
        created.id = 1
        created.username = "newuser"
        created.role = "user"
        mock_repo_instance.create.return_value = created
        mock_repo.return_value = mock_repo_instance

        with patch("app.api.auth.RuntimeProfileService.ensure_user_has_default_profile", return_value=None):
            response = client.post("/api/auth/register", json={"username": "newuser", "password": "password123"})
            assert response.status_code in [200, 201]


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


def test_register_provisions_default_runtime_profile(monkeypatch):
    from app.main import app
    import app.api.auth as auth_api
    from app.db import Base
    from app.models.user import User
    from app.models.runtime_profile import RuntimeProfile
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    def _override_db():
        yield db

    app.dependency_overrides[auth_api.get_db] = _override_db

    try:
        client = TestClient(app)
        resp = client.post("/api/auth/register", json={"username": "newbie", "password": "password123"})
        assert resp.status_code == 200

        user = db.scalar(select(User).where(User.username == "newbie"))
        assert user is not None
        profiles = list(db.scalars(select(RuntimeProfile).where(RuntimeProfile.owner_user_id == user.id)).all())
        assert len(profiles) == 1
        assert profiles[0].is_default is True
    finally:
        app.dependency_overrides.clear()
        db.close()
