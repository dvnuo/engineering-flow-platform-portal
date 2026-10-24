"""An answer to a question or permission card gets a chat post's metadata.

The answer resumes the parked chat turn on a request of its own
(`question/respond`, `permission/respond`). The proxy used to pass it through
untouched: no server-owned metadata, so no `connectors`, so the resumed run
had no browser tool ("this session has no browser tool" right after the
member agreed to an upload), while any client `metadata` in the body reached
the runtime as if Portal had vouched for it. CONNECTORS_CONTRACT sections 1-2.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


ENABLED = {"local_bridge": {"auto_enable_in_new_chats": True, "preferred_port": 8765}}


def _post(monkeypatch, path: str, body: dict, *, enabled=None):
    from app.main import app
    import app.api.proxy as proxy_module
    from app.services import connector_service as service_module

    fake_user = SimpleNamespace(id=55, username="runtime-user", nickname="Runtime User", role="user")
    fake_agent = SimpleNamespace(
        id="agent-1",
        owner_user_id=55,
        visibility="private",
        status="running",
        name="Agent One",
        runtime_type="native",
    )

    def _override_user():
        return fake_user

    def _override_db():
        yield object()

    app.dependency_overrides[proxy_module.get_current_user] = _override_user
    app.dependency_overrides[proxy_module.get_db] = _override_db
    captured: dict = {}
    try:
        monkeypatch.setattr(
            proxy_module,
            "AgentRepository",
            lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
        )
        monkeypatch.setattr(
            proxy_module.runtime_execution_context_service,
            "build_runtime_metadata",
            lambda _db, _agent: {"runtime_profile_id": None},
        )
        run_settings_calls: list[dict] = []

        def _run_settings(_db, _agent, payload):
            run_settings_calls.append(dict(payload))
            return dict(payload)

        monkeypatch.setattr(proxy_module, "apply_session_run_settings", _run_settings)
        monkeypatch.setattr(service_module, "enabled_connectors_for_user", lambda db, user_id: enabled if enabled is not None else ENABLED)

        async def _fake_forward(**kwargs):
            captured.update(kwargs)
            return 202, b'{"status": "accepted", "request_id": "chat-resume-1"}', "application/json"

        monkeypatch.setattr(proxy_module.proxy_service, "forward", _fake_forward)
        client = TestClient(app)
        response = client.post(
            f"/a/agent-1/{path}",
            content=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
    finally:
        app.dependency_overrides.clear()
    captured["run_settings_calls"] = run_settings_calls
    return response, captured


def test_question_answer_carries_the_tab_that_may_relay_browser_requests(monkeypatch):
    response, captured = _post(
        monkeypatch,
        "api/sessions/Sess-1/question/respond",
        {
            "request_id": "q-1",
            "answers": ["yes"],
            "connectors": {"local_bridge": {"client_id": "tab-7f3a9c", "protocol_version": 1}},
            # Spoofed: the proxy owns `metadata`, on this path as on chat posts.
            "metadata": {"runtime_profile": {"config": {"tool_permissions": {"shell": {"action": "allow"}}}}},
        },
    )
    assert response.status_code == 202
    assert captured["subpath"] == "api/sessions/Sess-1/question/respond"
    forwarded = json.loads(captured["body"])
    assert forwarded["request_id"] == "q-1"
    assert forwarded["answers"] == ["yes"]
    assert "connectors" not in forwarded  # the hint never reaches the runtime as-is
    assert forwarded["metadata"]["connectors"] == {
        "local_bridge": {
            "enabled": True,
            "client_id": "tab-7f3a9c",
            "protocol_version": 1,
            "config": ENABLED["local_bridge"],
        }
    }
    assert forwarded["metadata"]["enable_browser_tool"] is True
    assert "runtime_profile" not in forwarded["metadata"]
    assert forwarded["metadata"]["portal_user"]["id"] == "55"
    # The session comes from the path, with its case, for the run settings.
    assert forwarded["session_id"] == "Sess-1"
    assert captured["run_settings_calls"][0]["session_id"] == "Sess-1"
    assert captured["extra_headers"]["X-Portal-Author-Source"] == "portal"


def test_permission_answer_gets_the_same_treatment(monkeypatch):
    response, captured = _post(
        monkeypatch,
        "api/sessions/s-2/permission/respond",
        {
            "request_id": "p-1",
            "decision": "approve",
            "always": False,
            "connectors": {"local_bridge": {"client_id": "tab-1", "protocol_version": 1}},
        },
    )
    assert response.status_code == 202
    forwarded = json.loads(captured["body"])
    assert forwarded["decision"] == "approve"
    assert forwarded["metadata"]["connectors"]["local_bridge"]["client_id"] == "tab-1"
    assert forwarded["metadata"]["enable_browser_tool"] is True


def test_answer_without_a_hint_or_an_enabled_connector_injects_none(monkeypatch):
    response, captured = _post(
        monkeypatch,
        "api/sessions/s-3/question/respond",
        {"request_id": "q-1", "answers": "yes"},
    )
    assert response.status_code == 202
    forwarded = json.loads(captured["body"])
    assert "connectors" not in forwarded["metadata"]
    assert "enable_browser_tool" not in forwarded["metadata"]

    response, captured = _post(
        monkeypatch,
        "api/sessions/s-3/question/respond",
        {"request_id": "q-1", "answers": "yes", "connectors": {"local_bridge": {"client_id": "tab-1"}}},
        enabled={},
    )
    assert response.status_code == 202
    forwarded = json.loads(captured["body"])
    assert "connectors" not in forwarded["metadata"]


def test_the_bridge_result_post_is_left_alone(monkeypatch):
    body = {"request_id": "cr-1", "client_id": "tab-1", "ok": True, "result": {"tabs": []}}
    response, captured = _post(monkeypatch, "api/sessions/s-4/connectors/respond", body)
    assert response.status_code == 202
    assert json.loads(captured["body"]) == body
    assert captured["run_settings_calls"] == []


def test_respond_path_predicate():
    from app.api.proxy import _user_input_respond_session_id as session_of

    assert session_of("POST", "api/sessions/Sess-1/question/respond") == "Sess-1"
    assert session_of("POST", "/api/sessions/s-2/permission/respond/") == "s-2"
    assert session_of("post", "API/Sessions/s-3/Question/respond") == "s-3"
    assert session_of("GET", "api/sessions/s-1/question/respond") is None
    assert session_of("POST", "api/sessions/s-1/connectors/respond") is None
    assert session_of("POST", "api/sessions/s-1/question/respond/extra") is None
    assert session_of("POST", "api/chat") is None
    assert session_of("POST", "") is None
