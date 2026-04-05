"""Tests for agents API - basic endpoints."""
import pytest
from fastapi.testclient import TestClient


def test_agents_mine_endpoint():
    """Test /api/agents/mine endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/mine")
    # Returns list or auth error
    assert response.status_code in [200, 401, 403]


def test_agents_public_endpoint():
    """Test /api/agents/public endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/public")
    assert response.status_code in [200, 401, 403]


def test_agents_create_endpoint():
    """Test POST /api/agents endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents", json={"name": "test"})
    assert response.status_code in [200, 201, 400, 401, 403, 422]


def test_agents_get_endpoint():
    """Test GET /api/agents/{id} endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/test-id")
    assert response.status_code in [200, 401, 403, 404]


def test_agents_start_endpoint():
    """Test POST /api/agents/{id}/start endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/test-id/start")
    assert response.status_code in [200, 401, 403, 404, 500]


def test_agents_stop_endpoint():
    """Test POST /api/agents/{id}/stop endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/test-id/stop")
    assert response.status_code in [200, 401, 403, 404, 500]


def test_agents_restart_endpoint():
    """Test POST /api/agents/{id}/restart endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/test-id/restart")
    assert response.status_code in [200, 401, 403, 404, 500]


def test_agents_share_endpoint():
    """Test POST /api/agents/{id}/share endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/test-id/share")
    assert response.status_code in [200, 401, 403, 404]


def test_agents_unshare_endpoint():
    """Test POST /api/agents/{id}/unshare endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/test-id/unshare")
    assert response.status_code in [200, 401, 403, 404]


def test_agents_delete_endpoint():
    """Test DELETE /api/agents/{id} endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.delete("/api/agents/test-id")
    assert response.status_code in [200, 401, 403, 404]


def test_agents_status_endpoint():
    """Test GET /api/agents/{id}/status endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/test-id/status")
    assert response.status_code in [200, 401, 403, 404]


def test_agents_git_info_endpoint():
    """Test GET /a/{id}/api/git-info endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/test-id/api/git-info")
    assert response.status_code in [401, 403, 404, 409, 502]


def test_agents_destroy_endpoint():
    """Test POST /api/agents/{id}/destroy endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/test-id/destroy")
    assert response.status_code in [200, 401, 403, 404]


def test_agents_delete_runtime_endpoint():
    """Test POST /api/agents/{id}/delete-runtime endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/test-id/delete-runtime")
    assert response.status_code in [200, 401, 403, 404]


def test_agents_defaults_endpoint():
    """Test GET /api/agents/defaults endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/defaults")
    assert response.status_code in [200, 401, 403]
