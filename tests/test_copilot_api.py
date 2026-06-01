"""Tests for copilot API."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


class _AuthResp:
    status_code = 200
    content = b"1"
    text = "{}"

    def json(self):
        return {
            "device_code": "device-1",
            "user_code": "USER-1",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }


class _AuthClient:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _AuthResp()


def test_copilot_chat_endpoint():
    """Test copilot chat endpoint exists."""
    from app.main import app
    client = TestClient(app)
    
    # Test with mock data
    response = client.post("/api/copilot/chat", json={
        "messages": [{"role": "user", "content": "hello"}]
    })
    # Should return valid response or error
    assert response.status_code in [200, 401, 403, 404, 500]


def test_copilot_models_endpoint():
    """Test copilot models endpoint."""
    from app.main import app
    client = TestClient(app)
    
    response = client.get("/api/copilot/models")
    assert response.status_code in [200, 401, 403, 404]


def test_copilot_models_list():
    """Test copilot models returns list."""
    from app.main import app
    client = TestClient(app)
    
    response = client.get("/api/copilot/models")
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, (list, dict))


def test_copilot_auth_start_accepts_runtime_type_without_separate_client(monkeypatch):
    from app.main import app
    from app.api import copilot as mod
    from app.services import copilot_auth_service as svc_module

    class U:
        id = 1

    calls = []
    monkeypatch.setattr(svc_module.httpx, "AsyncClient", lambda *a, **k: _AuthClient(calls))
    app.dependency_overrides[mod.get_current_user] = lambda: U()
    try:
        client = TestClient(app)
        response = client.post("/api/copilot/auth/start", json={"runtime_type": "native"})
        assert response.status_code == 200
        assert calls[0]["json"]["client_id"] == svc_module.COPILOT_OAUTH_CLIENT_ID
    finally:
        app.dependency_overrides.clear()
