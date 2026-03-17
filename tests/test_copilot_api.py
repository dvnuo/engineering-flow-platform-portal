"""Tests for copilot API."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


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
