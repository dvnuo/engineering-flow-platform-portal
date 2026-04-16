import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


def _setup_client(monkeypatch, user, agent):
    from app.main import app
    import app.web as web_module

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _fake_forward_runtime(**_kwargs):
        payload = {
            "sessions": [
                {"session_id": "s-1", "name": "My Session", "last_message": "hello"},
            ]
        }
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    return TestClient(app)


def test_sessions_panel_renders_manage_actions_for_writable_user(monkeypatch):
    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    client = _setup_client(monkeypatch, user, agent)

    response = client.get("/app/agents/agent-1/sessions/panel")

    assert response.status_code == 200
    assert 'data-session-action="rename"' in response.text
    assert 'data-session-action="delete"' in response.text


def test_sessions_panel_hides_manage_actions_for_readonly_user(monkeypatch):
    user = SimpleNamespace(id=99, username="viewer", nickname="Viewer", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    client = _setup_client(monkeypatch, user, agent)

    response = client.get("/app/agents/agent-1/sessions/panel")

    assert response.status_code == 200
    assert 'data-session-action="rename"' not in response.text
    assert 'data-session-action="delete"' not in response.text
