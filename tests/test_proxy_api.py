"""Tests for proxy API endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


def test_proxy_chat():
    """Test /a/{agent_id}/api/chat proxy endpoint."""
    from app.main import app
    client = TestClient(app)
    
    # Without auth, should fail
    response = client.post("/a/test-agent/api/chat", json={
        "message": "test"
    })
    assert response.status_code in [401, 403, 404, 502]


def test_proxy_events():
    """Test /a/{agent_id}/api/events proxy endpoint."""
    from app.main import app
    client = TestClient(app)
    
    # WebSocket upgrade - should fail without proper connection
    response = client.get("/a/test-agent/api/events")
    # Should fail (not websocket upgrade)
    assert response.status_code in [400, 401, 403, 404]


def test_proxy_files_upload():
    """Test /a/{agent_id}/api/files/upload proxy endpoint."""
    from app.main import app
    client = TestClient(app)
    
    # Without auth, should fail
    response = client.post("/a/test-agent/api/files/upload")
    assert response.status_code in [401, 403, 404]


def test_proxy_files_preview():
    """Test /a/{agent_id}/api/files/{id}/preview proxy endpoint."""
    from app.main import app
    client = TestClient(app)
    
    response = client.get("/a/test-agent/api/files/test-file-id/preview")
    assert response.status_code in [401, 403, 404, 502]


def test_proxy_git_info():
    """Test /a/{agent_id}/api/git-info proxy endpoint."""
    from app.main import app
    client = TestClient(app)
    
    response = client.get("/a/test-agent/api/git-info")
    assert response.status_code in [401, 403, 404, 502]
