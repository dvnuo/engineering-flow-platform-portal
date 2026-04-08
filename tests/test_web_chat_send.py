import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_app_chat_send_forwards_identity_only_in_headers(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=123, username="alice", nickname=" Alice\r\n", role="user")
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

    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        return 200, json.dumps({"response": "hello", "session_id": "s-1", "events": []}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    monkeypatch.setattr(web_module.settings, "portal_internal_api_key", "portal-internal-key")
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {
            "capability_profile_id": "cap-web",
            "policy_profile_id": "pol-web",
            "allowed_capability_ids": ["tool:shell"],
            "policy_context": {"policy_profile_id": "pol-web"},
            "governance_require_explicit_allow": True,
        },
    )

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hi",
            "session_id": "s-1",
            "attachments": json.dumps([{"id": "file-1"}]),
        },
    )

    assert response.status_code == 200

    forwarded_payload = json.loads(captured["body"].decode("utf-8"))
    assert forwarded_payload["message"] == "hi"
    assert forwarded_payload["session_id"] == "s-1"
    assert forwarded_payload["attachments"] == [{"id": "file-1"}]
    assert "portal_user_id" not in forwarded_payload
    assert "portal_user_name" not in forwarded_payload
    assert forwarded_payload["metadata"]["capability_profile_id"] == "cap-web"
    assert forwarded_payload["metadata"]["policy_profile_id"] == "pol-web"
    assert forwarded_payload["metadata"]["policy_context"]["policy_profile_id"] == "pol-web"

    assert captured["extra_headers"]["X-Portal-Author-Source"] == "portal"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "123"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Alice"
    assert captured["extra_headers"]["X-Portal-Internal-Api-Key"] == "portal-internal-key"
    assert captured["headers"] == {"content-type": "application/json"}


def test_app_chat_send_drops_form_identity_and_uses_headers_only(monkeypatch):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id="  456 \r\n", username="fallback-name", nickname="\tBob\r\n", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=999,
        visibility="public",
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

    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        return 200, json.dumps({"response": "ok", "session_id": "s-2", "events": []}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)
    monkeypatch.setattr(web_module.settings, "portal_internal_api_key", "portal-internal-key")
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {
            "capability_profile_id": "cap-web",
            "policy_profile_id": "pol-web",
            "allowed_capability_ids": ["tool:shell"],
            "policy_context": {"policy_profile_id": "pol-web"},
        },
    )

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hello",
            "attachments": json.dumps([{"id": "file-2"}]),
            "portal_user_id": "spoofed",
            "portal_user_name": "spoofed",
        },
    )

    assert response.status_code == 200
    forwarded_payload = json.loads(captured["body"].decode("utf-8"))
    assert "portal_user_id" not in forwarded_payload
    assert "portal_user_name" not in forwarded_payload
    assert forwarded_payload["message"] == "hello"
    assert forwarded_payload["attachments"] == [{"id": "file-2"}]
    assert forwarded_payload["metadata"]["capability_profile_id"] == "cap-web"
    assert forwarded_payload["metadata"]["policy_profile_id"] == "pol-web"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "456"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Bob"
    assert captured["extra_headers"]["X-Portal-Internal-Api-Key"] == "portal-internal-key"


def test_app_chat_send_returns_503_when_portal_internal_api_key_missing(monkeypatch):
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
    monkeypatch.setattr(web_module.settings, "portal_internal_api_key", "")
    monkeypatch.setattr(
        web_module.runtime_execution_context_service,
        "build_runtime_metadata",
        lambda _db, _agent: {
            "capability_profile_id": "cap-web",
            "policy_profile_id": "pol-web",
        },
    )

    calls = {"count": 0}

    async def _fake_forward(**kwargs):
        calls["count"] += 1
        return 200, json.dumps({"response": "hello", "session_id": "s-1", "events": []}).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/app/chat/send",
        data={
            "agent_id": "agent-1",
            "message": "hi",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "PORTAL_INTERNAL_API_KEY is not configured"
    assert calls["count"] == 0
