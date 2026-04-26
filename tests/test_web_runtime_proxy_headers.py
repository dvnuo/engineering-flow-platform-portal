import json
import sys
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


def test_server_files_upload_uses_forward_multipart_with_identity_headers():
    from app.services.proxy_service import build_portal_agent_identity_headers

    fake_user = SimpleNamespace(id=321, username="portal-user", nickname="Portal User", role="user")
    fake_agent = SimpleNamespace(name="Agent One")

    assert build_portal_agent_identity_headers(fake_user, fake_agent) == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "321",
        "X-Portal-User-Name": "Portal User",
        "X-Portal-Agent-Name": "Agent One",
    }

    web_source = Path("app/web.py").read_text(encoding="utf-8")
    assert '@router.post("/a/{agent_id}/api/server-files/upload")' in web_source
    assert "_forward_runtime_multipart(" in web_source
    assert 'subpath="api/server-files/upload"' in web_source
    assert 'data={"path": target_path}' in web_source


def test_server_files_upload_enforces_write_access():
    web_source = Path("app/web.py").read_text(encoding="utf-8")
    fn_start = web_source.index("def _can_write(agent, user) -> bool:")
    fn_end = web_source.index("\n\ndef _portal_extra_headers", fn_start)
    fn_source = web_source[fn_start:fn_end]
    namespace: dict = {}
    exec(fn_source, namespace)
    can_write = namespace["_can_write"]

    agent = SimpleNamespace(owner_user_id=999)
    assert can_write(agent, SimpleNamespace(id=999, role="user")) is True
    assert can_write(agent, SimpleNamespace(id=111, role="admin")) is True
    assert can_write(agent, SimpleNamespace(id=111, role="user")) is False

    assert '@router.post("/a/{agent_id}/api/server-files/upload")' in web_source
    assert "if not _can_write(agent, user):" in web_source


def test_server_files_upload_passthrough_runtime_error():
    web_source = Path("app/web.py").read_text(encoding="utf-8")
    assert '@router.post("/a/{agent_id}/api/server-files/upload")' in web_source
    assert "_forward_runtime_multipart(" in web_source
    assert 'subpath="api/server-files/upload"' in web_source
    assert 'data={"path": target_path}' in web_source
    assert "return Response(content=content_bytes, media_type=content_type, status_code=status_code)" in web_source


def test_non_execution_runtime_proxy_path_keeps_body_untouched(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)

    captured_calls.clear()
    response = client.get("/app/agents/agent-1/files/panel")
    assert response.status_code == 200
    assert captured_calls[-1]["subpath"] == "api/files/list"
    assert captured_calls[-1]["body"] is None
    assert captured_calls[-1]["headers"] == {}
