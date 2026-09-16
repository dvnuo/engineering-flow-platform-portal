"""Chatbox upload proxy: allowlist enforcement and runtime verdict pass-through."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _wire(monkeypatch, forward_result, captured):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname="Alice", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=123,
        visibility="private",
        status="running",
        name="Agent One",
    )

    class _DB:
        def close(self):
            return None

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    async def _fake_forward_multipart(**kwargs):
        captured.update(kwargs)
        return forward_result

    monkeypatch.setattr(web_module, "_forward_runtime_multipart", _fake_forward_multipart)
    return TestClient(app)


def _upload(client, filename, content=b"hello", content_type="text/plain", session_id="s1"):
    return client.post(
        f"/a/agent-1/api/files/upload?session_id={session_id}&token=drop-me",
        files={"file": (filename, content, content_type)},
    )


def _runtime_json(status, payload):
    return status, json.dumps(payload).encode("utf-8"), "application/json"


def test_upload_proxy_forwards_an_allowed_file_with_its_session_id(monkeypatch):
    captured = {}
    client = _wire(monkeypatch, _runtime_json(201, {"success": True, "file_id": "f1", "filename": "notes.txt"}), captured)

    response = _upload(client, "notes.txt")

    assert response.status_code == 201
    assert response.json()["file_id"] == "f1"
    assert captured["subpath"] == "api/files/upload"
    assert captured["query_items"] == [("session_id", "s1")]
    assert captured["files"]["file"][0] == "notes.txt"
    assert captured["files"]["file"][1] == b"hello"


def test_upload_proxy_rejects_an_extension_off_the_allowlist_before_forwarding(monkeypatch):
    captured = {}
    client = _wire(monkeypatch, _runtime_json(201, {"success": True}), captured)

    response = _upload(client, "tool.exe", b"MZ", "application/octet-stream")

    assert response.status_code == 415
    assert response.json()["detail"] == (
        "File type .exe is not allowed. Allowed: jpg, jpeg, png, webp, gif, pdf, docx, xlsx, csv, txt"
    )
    assert captured == {}

    response = _upload(client, "README", b"no extension")
    assert response.status_code == 415
    assert response.json()["detail"].startswith("Files without an extension are not allowed.")
    assert captured == {}


def test_upload_proxy_allowlist_follows_settings(monkeypatch):
    import app.web as web_module

    captured = {}
    client = _wire(monkeypatch, _runtime_json(201, {"success": True, "file_id": "f-md"}), captured)
    monkeypatch.setattr(web_module.get_settings(), "chat_upload_extensions", "md")

    rejected = _upload(client, "notes.txt")
    assert rejected.status_code == 415
    assert rejected.json()["detail"] == "File type .txt is not allowed. Allowed: md"
    assert captured == {}

    accepted = _upload(client, "notes.md", b"# hi", "text/markdown")
    assert accepted.status_code == 201
    assert captured["files"]["file"][0] == "notes.md"


@pytest.mark.parametrize(
    "runtime_status, runtime_error",
    [
        (415, "File type .exe is not allowed. Allowed: txt"),
        (413, "File exceeds 25MB limit"),
        (400, "Filename is required"),
    ],
)
def test_upload_proxy_keeps_runtime_validation_verdicts(monkeypatch, runtime_status, runtime_error):
    captured = {}
    client = _wire(monkeypatch, _runtime_json(runtime_status, {"success": False, "error": runtime_error}), captured)

    response = _upload(client, "notes.txt")

    assert response.status_code == runtime_status
    detail = response.json()["detail"]
    assert detail.startswith("Upload failed: ")
    assert runtime_error in detail


def test_upload_proxy_explains_a_runtime_without_the_attachment_api(monkeypatch):
    captured = {}
    client = _wire(monkeypatch, (404, b"404: Not Found", "text/plain"), captured)

    response = _upload(client, "notes.txt")

    assert response.status_code == 502
    assert "does not expose the chat attachment API" in response.json()["detail"]


def test_upload_proxy_maps_other_runtime_failures_to_502(monkeypatch):
    captured = {}
    client = _wire(monkeypatch, _runtime_json(500, {"success": False, "error": "disk full"}), captured)

    response = _upload(client, "notes.txt")

    assert response.status_code == 502
    assert response.json()["detail"] == "Upload failed: disk full"
