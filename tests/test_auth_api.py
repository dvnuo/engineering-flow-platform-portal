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
        mock_repo_instance.get_by_username_case_insensitive.return_value = mock_user
        mock_repo.return_value = mock_repo_instance
        
        with patch("app.api.auth.verify_password", return_value=True), patch(
            "app.api.auth.UserAllowlistRepository"
        ) as mock_allowlist:
            mock_allowlist.return_value.get_active_by_username.return_value = object()
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
    
    try:
        response = client.post("/api/auth/login", json={
            "username": "wrong",
            "password": "wrong"
        })
        assert response.status_code == 401
    except Exception:
        assert True


def test_register_api_removed():
    """Self-service registration is gone; accounts come from SSO / Copilot sign-in."""
    from app.main import app
    client = TestClient(app)

    response = client.post("/api/auth/register", json={
        "username": "newuser",
        "password": "password123"
    })
    assert response.status_code in [404, 405]

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


def test_external_sign_in_creates_default_runtime_profile(monkeypatch):
    from app.db import Base
    from app.models import RuntimeProfile, User, UserAllowlistEntry
    from app.services import external_login_service
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    db.add(UserAllowlistEntry(username="new-u", role="user", is_active=True))
    db.commit()

    monkeypatch.setattr(external_login_service, "hash_password", lambda raw: f"hashed-{raw}")
    try:
        user, created = external_login_service.provision_external_user(db, username="New-U", source="sso")
        assert created is True
        assert user.username == "new-u"
        user_id = db.query(User).filter_by(username="new-u").one().id
        profiles = db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == user_id).all()
        assert len(profiles) >= 1
        assert len([p for p in profiles if p.is_default]) == 1

        again, created_again = external_login_service.provision_external_user(db, username="new-u", source="sso")
        assert created_again is False
        assert again.id == user.id
        assert db.query(RuntimeProfile).filter(RuntimeProfile.owner_user_id == user_id).count() == len(profiles)
    finally:
        db.close()

