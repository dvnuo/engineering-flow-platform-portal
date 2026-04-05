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
        elif subpath == "api/config":
            body = {"config": {}}
        elif subpath == "api/config/save":
            body = {"ok": True}
        elif subpath == "api/usage":
            body = {}
        elif subpath == "api/ssh/generate":
            body = {"public_key": "ssh-rsa AAA"}
        elif subpath == "api/ssh/public-key":
            body = {"public_key": "ssh-rsa AAA"}
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
        ("/app/agents/agent-1/settings/panel", "api/config"),
        ("/app/agents/agent-1/usage/panel?days=14", "api/usage"),
        ("/api/agents/agent-1/ssh/public-key", "api/ssh/public-key"),
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
        }


def test_runtime_post_routes_include_identity_headers_and_content_type(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)

    captured_calls.clear()
    settings_save = client.post(
        "/app/agents/agent-1/settings/save",
        data={"original_config_json": "{}", "llm_provider": "openai"},
    )
    assert settings_save.status_code == 200
    assert captured_calls[0]["subpath"] == "api/config/save"
    assert captured_calls[0]["headers"] == {"content-type": "application/json"}
    assert captured_calls[0]["extra_headers"]["X-Portal-User-Id"] == "321"

    captured_calls.clear()
    ssh_generate = client.post("/api/agents/agent-1/ssh/generate")
    assert ssh_generate.status_code == 200
    assert captured_calls[-1]["subpath"] == "api/ssh/generate"
    assert captured_calls[-1]["headers"] == {"content-type": "application/json"}
    assert captured_calls[-1]["extra_headers"]["X-Portal-Author-Source"] == "portal"


def test_file_upload_uses_forward_multipart_with_identity_headers(monkeypatch):
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
    }


def test_file_upload_enforces_10mb_limit(monkeypatch):
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
