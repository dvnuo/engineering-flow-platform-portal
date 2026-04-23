import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


def _setup_web_runtime_test(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname=" Portal User\n", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=321,
        visibility="private",
        status="running",
        name="Agent One",
        repo_url="https://example.com/repo.git",
    )

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    captured_calls = []

    async def _fake_forward(**kwargs):
        captured_calls.append(kwargs)
        subpath = kwargs.get("subpath")
        if subpath == "api/sessions":
            body = {"sessions": []}
        elif subpath == "api/files/list":
            body = {"files": []}
        elif subpath and subpath.endswith("/chatlog"):
            body = {"messages": []}
        elif subpath == "api/usage":
            body = {}
        else:
            body = {"ok": True}
        return 200, json.dumps(body).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    client = TestClient(app)
    return client, captured_calls


def test_runtime_panel_routes_include_identity_headers(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)

    routes = [
        ("/app/agents/agent-1/sessions/panel?limit=5", "api/sessions"),
        ("/app/agents/agent-1/files/panel", "api/files/list"),
        ("/app/agents/agent-1/thinking/panel?session_id=s-1", "api/sessions/s-1/chatlog"),
        ("/app/agents/agent-1/usage/panel?days=14", "api/usage"),
    ]

    for path, expected_subpath in routes:
        captured_calls.clear()
        response = client.get(path)
        assert response.status_code == 200
        assert captured_calls[-1]["subpath"] == expected_subpath
        assert captured_calls[-1]["extra_headers"] == {
            "X-Portal-Author-Source": "portal",
            "X-Portal-User-Id": "321",
            "X-Portal-User-Name": "Portal User",
            "X-Portal-Agent-Name": "Agent One",
        }


def test_settings_save_forbidden_for_public_non_owner(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="viewer", nickname="Viewer", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=999,
        visibility="public",
        status="running",
        name="Agent One",
        repo_url="https://example.com/repo.git",
        runtime_profile_id="rp-1",
    )

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    async def _should_not_sync(*_args, **_kwargs):
        raise AssertionError("sync should not be called for forbidden settings save")

    monkeypatch.setattr(web_module.runtime_profile_sync_service, "sync_profile_to_bound_agents", _should_not_sync)

    client = TestClient(app)
    response = client.post("/app/agents/agent-1/settings/save", data={})

    assert response.status_code == 403


def test_file_upload_uses_forward_multipart_with_identity_headers(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=321, visibility="private", status="running", name="Agent One")

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    captured = {}

    async def _fake_forward_multipart(**kwargs):
        captured.update(kwargs)
        return 200, b'{"ok": true}', "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward_multipart", _fake_forward_multipart)

    client = TestClient(app)
    response = client.post(
        "/a/agent-1/api/files/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 200
    assert captured["subpath"] == "api/files/upload"
    assert captured["files"]["file"][0] == "note.txt"
    assert captured["files"]["file"][2] == "text/plain"
    assert captured["extra_headers"] == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "321",
        "X-Portal-User-Name": "Portal User",
        "X-Portal-Agent-Name": "Agent One",
    }


def test_file_upload_forwards_session_id_query(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=321, visibility="private", status="running", name="Agent One")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent))

    captured = {}

    async def _fake_forward_multipart(**kwargs):
        captured.update(kwargs)
        return 200, b'{"ok": true}', "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward_multipart", _fake_forward_multipart)
    client = TestClient(app)
    response = client.post(
        "/a/agent-1/api/files/upload?session_id=sess-123",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 200
    assert captured["query_items"] == [("session_id", "sess-123")]


def test_file_upload_enforces_10mb_limit(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=321, visibility="private", status="running", name="Agent One")

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    async def _should_not_be_called(**kwargs):
        raise AssertionError("forward_multipart should not be called for oversized files")

    monkeypatch.setattr(web_module.proxy_service, "forward_multipart", _should_not_be_called)

    client = TestClient(app)
    too_large = b"x" * (10 * 1024 * 1024 + 1)
    response = client.post(
        "/a/agent-1/api/files/upload",
        files={"file": ("big.bin", too_large, "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "File too large" in response.json()["detail"]


def test_server_files_upload_uses_forward_multipart_with_identity_headers(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=321, visibility="private", status="running", name="Agent One")

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    captured = {}

    async def _fake_forward_multipart(**kwargs):
        captured.update(kwargs)
        return 200, b'{"success": true, "mode": "file_save"}', "application/json"

    async def _should_not_forward(**kwargs):
        raise AssertionError("generic forward should not be called for server-files upload")

    monkeypatch.setattr(web_module.proxy_service, "forward_multipart", _fake_forward_multipart)
    monkeypatch.setattr(web_module.proxy_service, "forward", _should_not_forward)

    client = TestClient(app)
    response = client.post(
        "/a/agent-1/api/server-files/upload",
        files={"file": ("notes.zip", b"PK\x03\x04zip-bytes", "application/zip"), "path": (None, "/workspace")},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True, "mode": "file_save"}
    assert captured["subpath"] == "api/server-files/upload"
    assert captured["files"]["file"][0] == "notes.zip"
    assert captured["files"]["file"][1] == b"PK\x03\x04zip-bytes"
    assert captured["files"]["file"][2] == "application/zip"
    assert captured["data"]["path"] == "/workspace"
    assert captured["extra_headers"] == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "321",
        "X-Portal-User-Name": "Portal User",
        "X-Portal-Agent-Name": "Agent One",
    }


def test_server_files_upload_enforces_write_access(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=999, visibility="private", status="running")
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    async def _fake_forward_multipart(**kwargs):
        return 200, b'{"success": true}', "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward_multipart", _fake_forward_multipart)
    client = TestClient(app)

    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=999, username="owner", nickname="Owner", role="user"),
    )
    owner_resp = client.post(
        "/a/agent-1/api/server-files/upload",
        files={"file": ("a.txt", b"abc", "text/plain"), "path": (None, "/workspace")},
    )
    assert owner_resp.status_code == 200

    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=111, username="viewer", nickname="Viewer", role="user"),
    )
    non_owner_resp = client.post(
        "/a/agent-1/api/server-files/upload",
        files={"file": ("a.txt", b"abc", "text/plain"), "path": (None, "/workspace")},
    )
    assert non_owner_resp.status_code == 403


def test_server_files_upload_passthrough_runtime_error(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=321, visibility="private", status="running")

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    async def _fake_forward_multipart(**kwargs):
        return 400, b'{"success": false, "error": "Uploaded ZIP file is empty"}', "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward_multipart", _fake_forward_multipart)

    client = TestClient(app)
    response = client.post(
        "/a/agent-1/api/server-files/upload",
        files={"file": ("empty.zip", b"", "application/zip"), "path": (None, "/workspace")},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"success": False, "error": "Uploaded ZIP file is empty"}


def test_non_execution_runtime_proxy_path_keeps_body_untouched(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)

    captured_calls.clear()
    response = client.get("/app/agents/agent-1/files/panel")
    assert response.status_code == 200
    assert captured_calls[-1]["subpath"] == "api/files/list"
    assert captured_calls[-1]["body"] is None
    assert captured_calls[-1]["headers"] == {}


def test_files_panel_forwards_session_id_to_runtime_list(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)
    response = client.get("/app/agents/agent-1/files/panel?session_id=sess-list")
    assert response.status_code == 200
    assert captured_calls[-1]["subpath"] == "api/files/list"
    assert captured_calls[-1]["query_items"] == [("session_id", "sess-list")]


def test_files_preview_forwards_session_id(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)
    response = client.get("/a/agent-1/api/files/file-1/preview?max_chars=123&session_id=sess-preview")
    assert response.status_code == 200
    assert captured_calls[-1]["subpath"] == "api/files/file-1/preview"
    assert ("session_id", "sess-preview") in captured_calls[-1]["query_items"]
    assert ("max_chars", "123") in captured_calls[-1]["query_items"]


def test_files_parse_proxy_forwards_json_and_session_id(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)
    payload = {"file_id": "file-1", "options": {}}
    response = client.post("/a/agent-1/api/files/parse?session_id=sess-parse", json=payload)
    assert response.status_code == 200
    assert captured_calls[-1]["subpath"] == "api/files/parse"
    assert captured_calls[-1]["query_items"] == [("session_id", "sess-parse")]
    assert captured_calls[-1]["headers"] == {"content-type": "application/json"}
    assert json.loads(captured_calls[-1]["body"].decode("utf-8")) == payload
