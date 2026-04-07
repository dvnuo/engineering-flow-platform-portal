import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_app_chat_send_forwards_identity_in_headers_and_body_fallback(monkeypatch):
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
    assert forwarded_payload["portal_user_id"] == "123"
    assert forwarded_payload["portal_user_name"] == "Alice"
    assert forwarded_payload["metadata"]["capability_profile_id"] == "cap-web"
    assert forwarded_payload["metadata"]["policy_profile_id"] == "pol-web"
    assert forwarded_payload["metadata"]["policy_context"]["policy_profile_id"] == "pol-web"

    assert captured["extra_headers"]["X-Portal-Author-Source"] == "portal"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "123"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Alice"
    assert captured["headers"] == {"content-type": "application/json"}


def test_app_chat_send_body_identity_is_server_derived_and_sanitized(monkeypatch):
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
    assert forwarded_payload["portal_user_id"] == "456"
    assert forwarded_payload["portal_user_name"] == "Bob"
    assert forwarded_payload["message"] == "hello"
    assert forwarded_payload["attachments"] == [{"id": "file-2"}]
    assert forwarded_payload["metadata"]["capability_profile_id"] == "cap-web"
    assert forwarded_payload["metadata"]["policy_profile_id"] == "pol-web"
    assert captured["extra_headers"]["X-Portal-User-Id"] == "456"
    assert captured["extra_headers"]["X-Portal-User-Name"] == "Bob"
