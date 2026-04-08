from types import SimpleNamespace
import json

from fastapi.testclient import TestClient


def test_proxy_agent_injects_trusted_identity_headers(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=55, username="runtime-user", nickname=" Runtime User\n", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=55,
        visibility="private",
        status="running",
        capability_profile_id=None,
        policy_profile_id=None,
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    try:
        monkeypatch.setattr(
            proxy_module,
            "AgentRepository",
            lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
        )

        monkeypatch.setattr(
            proxy_module.runtime_execution_context_service,
            "build_runtime_metadata",
            lambda _db, _agent: {"capability_profile_id": None, "policy_profile_id": None, "policy_context": {"policy_profile_id": None}},
        )
        monkeypatch.setattr(proxy_module.settings, "portal_internal_api_key", "portal-internal-key")

        captured = {}

        async def _fake_forward(**kwargs):
            captured.update(kwargs)
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)

        client = TestClient(app)
        response = client.post(
            "/a/agent-1/api/chat?stream=runtime&token=secret&Token=secret2&TOKEN=secret3&stream=runtime2",
            content=b'{"message":"hello"}',
            headers={
                "content-type": "application/json",
                "x-forwarded-for": "1.2.3.4",
                "authorization": "Bearer browser-token",
                "x-portal-user-id": "spoofed",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["headers"] == {"content-type": "application/json"}
    assert captured["query_items"] == [("stream", "runtime"), ("stream", "runtime2")]
    assert captured["extra_headers"]["X-Portal-Author-Source"] == "portal"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "55"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Runtime User"
    assert captured["extra_headers"]["X-Portal-Internal-Api-Key"] == "portal-internal-key"


def test_proxy_agent_restricts_sensitive_ssh_endpoints_for_non_owner(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=99, username="viewer", nickname="Viewer", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=55,
        visibility="public",
        status="running",
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    try:
        monkeypatch.setattr(
            proxy_module,
            "AgentRepository",
            lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
        )

        async def _fake_forward(**kwargs):
            if kwargs.get("subpath") in {"api/ssh/public-key", "api/ssh/generate", "api/config/save"}:
                raise AssertionError("Forward should not be called for forbidden sensitive endpoints")
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)
        client = TestClient(app)

        read_resp = client.get("/a/agent-1/api/ssh/public-key")
        write_resp = client.post("/a/agent-1/api/ssh/generate")
        config_save_resp = client.post("/a/agent-1/api/config/save", content=b"{}")
        normal_resp = client.post("/a/agent-1/api/chat", content=b'{"message":"hello"}')
    finally:
        app.dependency_overrides.clear()

    assert read_resp.status_code == 403
    assert write_resp.status_code == 403
    assert config_save_resp.status_code == 403
    assert normal_resp.status_code == 200


def test_proxy_agent_allows_sensitive_ssh_endpoints_for_owner(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=55, username="owner", nickname="Owner", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=55,
        visibility="public",
        status="running",
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    try:
        monkeypatch.setattr(
            proxy_module,
            "AgentRepository",
            lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
        )

        captured = []

        async def _fake_forward(**kwargs):
            captured.append(kwargs)
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)
        client = TestClient(app)

        read_resp = client.get("/a/agent-1/api/ssh/public-key")
        write_resp = client.post("/a/agent-1/api/ssh/generate")
        config_save_resp = client.post("/a/agent-1/api/config/save", content=b"{}")
        normal_resp = client.get("/a/agent-1/api/usage")
    finally:
        app.dependency_overrides.clear()

    assert read_resp.status_code == 200
    assert write_resp.status_code == 200
    assert config_save_resp.status_code == 200
    assert normal_resp.status_code == 200
    assert captured[0]["subpath"] == "api/ssh/public-key"
    assert captured[1]["subpath"] == "api/ssh/generate"
    assert captured[2]["subpath"] == "api/config/save"
    assert captured[3]["subpath"] == "api/usage"


def test_requires_write_access_normalizes_slashes():
    import app.api.proxy as proxy_module

    assert proxy_module._requires_write_access("GET", "api/ssh/public-key")
    assert proxy_module._requires_write_access("GET", "/api/ssh/public-key")
    assert proxy_module._requires_write_access("GET", "api/ssh/public-key/")
    assert proxy_module._requires_write_access("GET", "/api/ssh/public-key/")

    assert proxy_module._requires_write_access("POST", "api/ssh/generate")
    assert proxy_module._requires_write_access("POST", "/api/ssh/generate/")
    assert proxy_module._requires_write_access("POST", "api/config/save")
    assert proxy_module._requires_write_access("POST", "/api/config/save/")
    assert not proxy_module._requires_write_access("POST", "api/chat")


def test_proxy_direct_chat_overrides_client_metadata_with_server_runtime_context(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=77, username="runtime-user", nickname="Runtime User", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=77,
        visibility="private",
        status="running",
        capability_profile_id="cap-1",
        policy_profile_id="pol-1",
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    try:
        monkeypatch.setattr(
            proxy_module,
            "AgentRepository",
            lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
        )
        monkeypatch.setattr(proxy_module.settings, "portal_internal_api_key", "portal-internal-key")
        monkeypatch.setattr(
            proxy_module.runtime_execution_context_service,
            "build_runtime_metadata",
            lambda _db, _agent: {
                "capability_profile_id": "server-cap",
                "policy_profile_id": "server-pol",
                "allowed_capability_ids": ["tool:shell"],
                "policy_context": {"policy_profile_id": "server-pol"},
                "governance_require_explicit_allow": True,
            },
        )

        captured = {}

        async def _fake_forward(**kwargs):
            captured.update(kwargs)
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)

        client = TestClient(app)
        response = client.post(
            "/a/agent-1/api/chat",
            json={
                "message": "hello",
                "portal_user_id": "spoofed",
                "portal_user_name": "spoofed",
                "metadata": {
                    "capability_profile_id": "fake",
                    "policy_context": {"policy_profile_id": "fake"},
                    "governance_require_explicit_allow": False,
                },
                "capability_context": {"allowed_capability_ids": ["fake"]},
                "policy_context": {"policy_profile_id": "fake"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    forwarded_payload = json.loads(captured["body"].decode("utf-8"))
    assert "portal_user_id" not in forwarded_payload
    assert "portal_user_name" not in forwarded_payload
    assert forwarded_payload["metadata"]["capability_profile_id"] == "server-cap"
    assert forwarded_payload["metadata"]["policy_profile_id"] == "server-pol"
    assert forwarded_payload["metadata"]["governance_require_explicit_allow"] is True
    assert "capability_context" not in forwarded_payload
    assert "policy_context" not in forwarded_payload
    assert captured["extra_headers"]["X-Portal-Author-Source"] == "portal"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "77"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Runtime User"
    assert captured["extra_headers"]["X-Portal-Internal-Api-Key"] == "portal-internal-key"


def test_proxy_direct_chat_rejects_malformed_json_without_forwarding(monkeypatch):
    from app.main import app
    import app.api.proxy as proxy_module

    fake_user = SimpleNamespace(id=77, username="runtime-user", nickname="Runtime User", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=77,
        visibility="private",
        status="running",
        capability_profile_id="cap-1",
        policy_profile_id="pol-1",
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    try:
        monkeypatch.setattr(
            proxy_module,
            "AgentRepository",
            lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
        )
        monkeypatch.setattr(proxy_module.settings, "portal_internal_api_key", "portal-internal-key")

        calls = {"count": 0}

        async def _fake_forward(**kwargs):
            calls["count"] += 1
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)

        client = TestClient(app)
        response = client.post(
            "/a/agent-1/api/chat",
            content=b'{"message":',
            headers={"content-type": "application/json"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid JSON payload"
    assert calls["count"] == 0


def test_build_runtime_internal_headers_returns_only_internal_header(monkeypatch):
    import app.deps as deps_module
    from app.services.proxy_service import build_runtime_internal_headers

    original = deps_module.settings.runtime_internal_api_key
    deps_module.settings.runtime_internal_api_key = "runtime-s2s-key"
    try:
        headers = build_runtime_internal_headers()
        assert headers == {"X-Internal-Api-Key": "runtime-s2s-key"}
    finally:
        deps_module.settings.runtime_internal_api_key = original


def test_runtime_internal_header_is_not_forwarded_in_browser_proxy_allowlist():
    from app.services.proxy_service import ProxyService

    outbound = ProxyService._build_outbound_headers(
        headers={"content-type": "application/json"},
        extra_headers={"X-Internal-Api-Key": "runtime-s2s-key", "X-Portal-User-Id": "10"},
    )

    assert outbound["content-type"] == "application/json"
    assert outbound["X-Portal-User-Id"] == "10"
    assert "X-Internal-Api-Key" not in outbound


def test_build_portal_execution_headers_adds_optional_internal_key(monkeypatch):
    import app.deps as deps_module
    from app.services.proxy_service import build_portal_execution_headers

    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = " internal-key\n"
    try:
        user = SimpleNamespace(id=" 55 ", username="user", nickname=" Name\n")
        headers = build_portal_execution_headers(user)
        assert headers["X-Portal-Author-Source"] == "portal"
        assert headers["X-Portal-User-Id"] == "55"
        assert headers["X-Portal-User-Name"] == "Name"
        assert headers["X-Portal-Internal-Api-Key"] == "internal-key"
    finally:
        deps_module.settings.portal_internal_api_key = original


def test_build_portal_execution_headers_omits_internal_key_when_unset(monkeypatch):
    import app.deps as deps_module
    from app.services.proxy_service import build_portal_execution_headers

    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = ""
    try:
        user = SimpleNamespace(id=1, username="alice", nickname=None)
        headers = build_portal_execution_headers(user)
        assert headers["X-Portal-Author-Source"] == "portal"
        assert headers["X-Portal-User-Id"] == "1"
        assert headers["X-Portal-User-Name"] == "alice"
        assert "X-Portal-Internal-Api-Key" not in headers
    finally:
        deps_module.settings.portal_internal_api_key = original


def test_require_internal_api_key_strips_whitespace_and_accepts_match():
    import app.deps as deps_module

    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = " expected-key "
    try:
        assert deps_module.require_internal_api_key(" expected-key\n") is True
    finally:
        deps_module.settings.portal_internal_api_key = original


def test_require_internal_api_key_rejects_wrong_value_with_401():
    import app.deps as deps_module
    from fastapi import HTTPException

    original = deps_module.settings.portal_internal_api_key
    deps_module.settings.portal_internal_api_key = "expected-key"
    try:
        try:
            deps_module.require_internal_api_key("wrong-key")
        except HTTPException as exc:
            assert exc.status_code == 401
            assert exc.detail == "Invalid internal API key"
        else:
            raise AssertionError("expected HTTPException for invalid internal API key")
    finally:
        deps_module.settings.portal_internal_api_key = original
