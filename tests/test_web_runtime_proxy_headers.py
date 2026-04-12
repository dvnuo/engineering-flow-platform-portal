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
        data={
            "original_config_json": json.dumps({
                "llm": {"api_base": "https://custom.example/v1", "api_key": "keep-llm-key"},
                "github": {"api_token": "keep-github-token"},
                "proxy": {"url": "http://proxy.local", "username": "proxy-user", "password": "keep-secret"},
                "jira": {"instances": [{"name": "Jira", "url": "https://jira", "password": "jira-pass", "token": "jira-token"}]},
                "confluence": {"instances": [{"name": "Conf", "url": "https://conf", "password": "conf-pass", "token": "conf-token"}]},
                "debug": {"log_level": "ERROR"},
                "runtime_extra": {"keep": True},
            }),
            "llm_provider": "openai",
            "llm_api_key": "",
            "github_api_token": "",
            "proxy_url": "",
            "proxy_username": "",
            "proxy_password": "",
            "debug_log_level": "NOPE",
            "jira_instance_count": "1",
            "jira_instances_0_name": "Jira",
            "jira_instances_0_url": "https://jira",
            "jira_instances_0_username": "",
            "jira_instances_0_password": "",
            "jira_instances_0_token": "",
            "jira_instances_0_project": "",
            "confluence_instance_count": "1",
            "confluence_instances_0_name": "Conf",
            "confluence_instances_0_url": "https://conf",
            "confluence_instances_0_username": "",
            "confluence_instances_0_password": "",
            "confluence_instances_0_token": "",
            "confluence_instances_0_space": "",
        },
    )
    assert settings_save.status_code == 200
    assert captured_calls[0]["subpath"] == "api/config/save"
    assert captured_calls[0]["headers"] == {"content-type": "application/json"}
    assert captured_calls[0]["extra_headers"]["X-Portal-User-Id"] == "321"
    settings_payload = json.loads(captured_calls[0]["body"].decode("utf-8"))
    assert settings_payload["llm"]["api_base"] == "https://custom.example/v1"
    assert settings_payload["llm"]["api_key"] == "keep-llm-key"
    assert settings_payload["github"]["api_token"] == "keep-github-token"
    assert settings_payload["proxy"]["url"] == ""
    assert settings_payload["proxy"]["username"] == ""
    assert "password" not in settings_payload["proxy"]
    assert settings_payload["jira"]["instances"][0]["password"] == "jira-pass"
    assert settings_payload["jira"]["instances"][0]["token"] == "jira-token"
    assert settings_payload["jira"]["instances"][0]["username"] == ""
    assert settings_payload["confluence"]["instances"][0]["password"] == "conf-pass"
    assert settings_payload["confluence"]["instances"][0]["token"] == "conf-token"
    assert settings_payload["confluence"]["instances"][0]["username"] == ""
    assert settings_payload["debug"]["log_level"] == "ERROR"
    assert settings_payload["runtime_extra"] == {"keep": True}


def test_settings_save_preserves_proxy_password_when_field_absent(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)
    captured_calls.clear()
    response = client.post(
        "/app/agents/agent-1/settings/save",
        data={
            "original_config_json": json.dumps({
                "proxy": {"password": "keep-secret"},
            }),
        },
    )
    assert response.status_code == 200
    settings_payload = json.loads(captured_calls[0]["body"].decode("utf-8"))
    assert settings_payload["proxy"]["password"] == "keep-secret"


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
    )

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _should_not_forward(**kwargs):
        raise AssertionError("forward should not be called for forbidden settings save")

    monkeypatch.setattr(web_module.proxy_service, "forward", _should_not_forward)

    client = TestClient(app)
    response = client.post(
        "/app/agents/agent-1/settings/save",
        data={"original_config_json": json.dumps({})},
    )

    assert response.status_code == 403


def test_settings_save_allowed_for_admin_non_owner(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=1, username="admin", nickname="Admin", role="admin")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=999,
        visibility="public",
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
        if kwargs.get("subpath") == "api/config":
            return 200, json.dumps({"config": {}}).encode("utf-8"), "application/json"
        return 200, json.dumps({"ok": True}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/app/agents/agent-1/settings/save",
        data={"original_config_json": json.dumps({})},
    )

    assert response.status_code == 200
    assert captured_calls[0]["subpath"] == "api/config/save"


def test_settings_save_does_not_infer_llm_api_base(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)
    captured_calls.clear()
    response = client.post(
        "/app/agents/agent-1/settings/save",
        data={
            "original_config_json": json.dumps({"llm": {}}),
            "llm_provider": "anthropic",
            "llm_model": "claude",
        },
    )
    assert response.status_code == 200
    settings_payload = json.loads(captured_calls[0]["body"].decode("utf-8"))
    assert "api_base" not in settings_payload.get("llm", {})


def test_settings_save_drops_instance_when_name_and_url_are_blank(monkeypatch):
    client, captured_calls = _setup_web_runtime_test(monkeypatch)
    captured_calls.clear()
    response = client.post(
        "/app/agents/agent-1/settings/save",
        data={
            "original_config_json": json.dumps({
                "jira": {"instances": [{"name": "Jira", "url": "https://jira", "password": "jira-pass", "token": "jira-token"}]},
            }),
            "jira_instance_count": "1",
            "jira_instances_0_name": "",
            "jira_instances_0_url": "",
            "jira_instances_0_username": "",
            "jira_instances_0_password": "",
            "jira_instances_0_token": "",
            "jira_instances_0_project": "",
        },
    )
    assert response.status_code == 200
    settings_payload = json.loads(captured_calls[0]["body"].decode("utf-8"))
    assert settings_payload["jira"]["instances"] == []


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


def test_server_files_upload_uses_forward_multipart_with_identity_headers(monkeypatch):
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
