import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_app_chat_send_forwards_identity_in_headers_and_body(monkeypatch):
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

    assert captured["extra_headers"] == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "123",
        "X-Portal-User-Name": "Alice",
    }
    assert captured["headers"] == {"content-type": "application/json"}
