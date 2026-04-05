from types import SimpleNamespace

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
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db

    monkeypatch.setattr(
        proxy_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )

    captured = {}

    async def _fake_forward(**kwargs):
        captured.update(kwargs)
        return 200, b'{"ok": true}', "application/json"

    monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)

    client = TestClient(app)
    response = client.post(
        "/a/agent-1/api/chat?stream=runtime",
        content=b'{"message":"hello"}',
        headers={
            "content-type": "application/json",
            "x-forwarded-for": "1.2.3.4",
            "authorization": "Bearer browser-token",
            "x-portal-user-id": "spoofed",
        },
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["headers"] == {"content-type": "application/json"}
    assert captured["extra_headers"] == {
        "X-Portal-Author-Source": "portal",
        "X-Portal-User-Id": "55",
        "X-Portal-User-Name": "Runtime User",
    }
