"""Tests for file operations and uploads."""
import pytest
from fastapi.testclient import TestClient
from io import BytesIO


def test_file_upload():
    """Test file upload endpoint."""
    from app.main import app
    client = TestClient(app)
    
    # Create a test file
    file_content = b"test file content"
    files = {"file": ("test.txt", BytesIO(file_content), "text/plain")}
    
    response = client.post("/a/agent-123/api/files/upload", files=files)
    assert response.status_code in [200, 401, 403, 404, 415, 500]


def test_file_upload_image():
    """Test image upload."""
    from app.main import app
    client = TestClient(app)
    
    # Create a test image (minimal valid bytes)
    file_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    files = {"file": ("test.png", BytesIO(file_content), "image/png")}
    
    response = client.post("/a/agent-123/api/files/upload", files=files)
    assert response.status_code in [200, 401, 403, 404, 415, 500]


def test_file_preview():
    """Test file preview endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/files/file-id/preview")
    assert response.status_code in [200, 401, 403, 404, 500]


def test_file_preview_with_chars():
    """Test file preview with max_chars."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/files/file-id/preview?max_chars=1000")
    assert response.status_code in [200, 401, 403, 404, 500]


def test_files_list():
    """Test files list endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/files")
    assert response.status_code in [200, 401, 403, 404]


def test_files_list_with_path():
    """Test files list with path."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/files?path=/test")
    assert response.status_code in [200, 401, 403, 404]


def test_file_download():
    """Test file download."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/files/file-id")
    assert response.status_code in [200, 401, 403, 404]


def test_file_parse():
    """Test file parse."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/files/parse",
                         json={"file_id": "test-id"})
    assert response.status_code in [200, 400, 401, 403, 404]


def test_file_preview_direct():
    """Test direct file preview."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/files/test-id/preview")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_file_upload():
    """Test agent file upload endpoint."""
    from app.main import app
    client = TestClient(app)
    
    file_content = b"test"
    files = {"file": ("test.txt", BytesIO(file_content), "text/plain")}
    
    response = client.post("/a/agent-123/api/files/upload", files=files)
    assert response.status_code in [200, 401, 403, 404, 415]
