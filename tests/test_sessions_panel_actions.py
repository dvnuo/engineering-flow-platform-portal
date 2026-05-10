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
    monkeypatch.setattr(
        web_module,
        "AgentSessionMetadataRepository",
        lambda _db: SimpleNamespace(
            list_by_agent_and_session_ids=lambda **_kwargs: [],
            list_by_agent=lambda *_args, **_kwargs: [],
        ),
    )
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


def test_sessions_panel_renders_context_preview_when_metadata_exists(monkeypatch):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _fake_forward_runtime(**_kwargs):
        payload = {"sessions": [{"session_id": "s-1", "name": "My Session", "last_message": "runtime fallback"}]}
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    metadata_json = json.dumps(
        {
            "context_compaction_level": "high",
            "context_objective_preview": "Ship portal context preview",
            "context_summary_preview": "Summarized context from runtime metadata",
            "context_next_step_preview": "Validate tests and merge",
        }
    )
    metadata_record = SimpleNamespace(
        session_id="s-1",
        latest_event_state="running",
        snapshot_version=3,
        metadata_json=metadata_json,
    )
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(
        web_module,
        "AgentSessionMetadataRepository",
        lambda _db: SimpleNamespace(
            list_by_agent_and_session_ids=lambda **_kwargs: [metadata_record],
            list_by_agent=lambda *_args, **_kwargs: [metadata_record],
        ),
    )

    client = TestClient(app)
    response = client.get("/app/agents/agent-1/sessions/panel")

    assert response.status_code == 200
    assert "Summarized context from runtime metadata" in response.text
    assert "Next: Validate tests and merge" in response.text
    assert "running" in response.text
    assert "high" in response.text


def test_sessions_panel_renders_active_skill_preview_when_metadata_exists(monkeypatch):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _fake_forward_runtime(**_kwargs):
        payload = {"sessions": [{"session_id": "s-1", "name": "My Session", "last_message": "runtime fallback"}]}
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    metadata_record = SimpleNamespace(
        session_id="s-1",
        latest_event_state="running",
        snapshot_version=3,
        metadata_json='{"active_skill_name":"review-pull-request","active_skill_status":"active","active_skill_goal":"Review PR #12","active_skill_turn_count":2,"active_skill_activation_reason":"continued"}',
    )
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(
        web_module,
        "AgentSessionMetadataRepository",
        lambda _db: SimpleNamespace(
            list_by_agent_and_session_ids=lambda **_kwargs: [metadata_record],
            list_by_agent=lambda *_args, **_kwargs: [metadata_record],
        ),
    )

    client = TestClient(app)
    response = client.get("/app/agents/agent-1/sessions/panel")

    assert response.status_code == 200
    assert "Skill: review-pull-request" in response.text
    assert "Review PR #12" in response.text
    assert "Active skill turn 2" in response.text
    assert "continued" in response.text


def test_sessions_panel_handles_invalid_metadata_json(monkeypatch):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _fake_forward_runtime(**_kwargs):
        payload = {"sessions": [{"session_id": "s-1", "name": "My Session", "last_message": "runtime fallback"}]}
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    metadata_record = SimpleNamespace(
        session_id="s-1",
        latest_event_state="running",
        snapshot_version=3,
        metadata_json="{not-valid-json",
    )
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(
        web_module,
        "AgentSessionMetadataRepository",
        lambda _db: SimpleNamespace(
            list_by_agent_and_session_ids=lambda **_kwargs: [metadata_record],
            list_by_agent=lambda *_args, **_kwargs: [metadata_record],
        ),
    )

    client = TestClient(app)
    response = client.get("/app/agents/agent-1/sessions/panel")

    assert response.status_code == 200
    assert "runtime fallback" in response.text


def test_sessions_panel_renders_metadata_only_session_fallback(monkeypatch):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _fake_forward_runtime(**_kwargs):
        payload = {"sessions": [{"session_id": "s-1", "name": "My Session", "last_message": "runtime text"}]}
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    runtime_metadata_record = SimpleNamespace(
        session_id="s-1",
        latest_event_state="done",
        snapshot_version=1,
        metadata_json='{"context_summary_preview":"Runtime summary"}',
    )
    metadata_only_record = SimpleNamespace(
        session_id="s-2",
        latest_event_state="running",
        snapshot_version=2,
        metadata_json='{"context_summary_preview":"Metadata-only preview"}',
    )
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(
        web_module,
        "AgentSessionMetadataRepository",
        lambda _db: SimpleNamespace(
            list_by_agent_and_session_ids=lambda **_kwargs: [runtime_metadata_record],
            list_by_agent=lambda *_args, **_kwargs: [metadata_only_record, runtime_metadata_record],
        ),
    )

    client = TestClient(app)
    response = client.get("/app/agents/agent-1/sessions/panel")

    assert response.status_code == 200
    assert "Metadata-only preview" in response.text
    assert "metadata only" in response.text


def test_sessions_panel_renders_metadata_only_sessions_when_k8s_disabled(monkeypatch):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", False)

    metadata_record = SimpleNamespace(
        session_id="s-2",
        latest_event_state="running",
        snapshot_version=2,
        metadata_json='{"context_summary_preview":"K8s disabled preview","context_compaction_level":"high"}',
    )
    monkeypatch.setattr(
        web_module,
        "AgentSessionMetadataRepository",
        lambda _db: SimpleNamespace(
            list_by_agent=lambda *_args, **_kwargs: [metadata_record],
            list_by_agent_and_session_ids=lambda **_kwargs: [],
        ),
    )

    client = TestClient(app)
    response = client.get("/app/agents/agent-1/sessions/panel")

    assert response.status_code == 200
    assert "K8s disabled preview" in response.text
    assert "metadata only" in response.text

def test_control_plane_delete_session_runtime_404_marks_metadata_deleted(monkeypatch):
    from app.main import app
    import app.web as web_module

    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _fake_forward_runtime(**_kwargs):
        return 404, b'{"detail":"not found"}', "application/json"

    calls = {}
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(web_module, "AgentSessionMetadataRepository", lambda _db: SimpleNamespace(mark_deleted=lambda **kwargs: (SimpleNamespace(deleted_at=__import__('datetime').datetime.utcnow()), False)))
    client = TestClient(app)
    resp = client.delete('/app/agents/agent-1/sessions/s-zombie')
    assert resp.status_code == 200
    assert resp.json()["runtime_missing"] is True
    assert resp.json()["metadata_deleted"] is True


def test_control_plane_delete_session_runtime_500_does_not_mark_deleted(monkeypatch):
    from app.main import app
    import app.web as web_module
    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)
    async def _fake_forward_runtime(**_kwargs):
        return 500, b'{"error":"boom"}', "application/json"
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    marker = {"called": False}
    monkeypatch.setattr(web_module, "AgentSessionMetadataRepository", lambda _db: SimpleNamespace(mark_deleted=lambda **kwargs: marker.__setitem__("called", True)))
    client = TestClient(app)
    resp = client.delete('/app/agents/agent-1/sessions/s-zombie')
    assert resp.status_code == 502
    assert marker["called"] is False

def test_control_plane_delete_session_runtime_success_false_returns_502(monkeypatch):
    from app.main import app
    import app.web as web_module
    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)
    async def _fake_forward_runtime(**_kwargs):
        return 200, b'{"success":false,"detail":"bad"}', "application/json"
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    marker = {"called": False}
    monkeypatch.setattr(web_module, "AgentSessionMetadataRepository", lambda _db: SimpleNamespace(mark_deleted=lambda **kwargs: marker.__setitem__("called", True)))
    client = TestClient(app)
    resp = client.delete('/app/agents/agent-1/sessions/s-zombie')
    assert resp.status_code == 502
    assert marker["called"] is False


def test_control_plane_delete_session_runtime_invalid_json_is_accepted(monkeypatch):
    from app.main import app
    import app.web as web_module
    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)
    async def _fake_forward_runtime(**_kwargs):
        return 200, b'plain-text-ok', "text/plain"
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(web_module, "AgentSessionMetadataRepository", lambda _db: SimpleNamespace(mark_deleted=lambda **kwargs: (SimpleNamespace(deleted_at=__import__('datetime').datetime.utcnow()), False)))
    client = TestClient(app)
    resp = client.delete('/app/agents/agent-1/sessions/s-zombie')
    assert resp.status_code == 200
    assert resp.json()["runtime_response_json"] is False


def test_control_plane_delete_session_k8s_disabled_skips_runtime(monkeypatch):
    from app.main import app
    import app.web as web_module
    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(web_module.settings, "k8s_enabled", False)
    called = {"runtime": False, "mark": False}
    async def _fake_forward_runtime(**_kwargs):
        called["runtime"] = True
        return 200, b'{}', "application/json"
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(web_module, "AgentSessionMetadataRepository", lambda _db: SimpleNamespace(mark_deleted=lambda **kwargs: (called.__setitem__("mark", True) or (SimpleNamespace(deleted_at=__import__('datetime').datetime.utcnow()), False))))
    client = TestClient(app)
    resp = client.delete('/app/agents/agent-1/sessions/s-zombie')
    assert resp.status_code == 200
    assert resp.json()["runtime_skipped"] is True
    assert called["runtime"] is False
    assert called["mark"] is True


def test_control_plane_delete_session_no_write_permission_does_not_call_runtime(monkeypatch):
    from app.main import app
    import app.web as web_module
    user = SimpleNamespace(id=99, username="viewer", nickname="Viewer", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)
    called = {"runtime": False, "mark": False}
    async def _fake_forward_runtime(**_kwargs):
        called["runtime"] = True
        return 200, b'{}', "application/json"
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(web_module, "AgentSessionMetadataRepository", lambda _db: SimpleNamespace(mark_deleted=lambda **kwargs: called.__setitem__("mark", True)))
    client = TestClient(app)
    resp = client.delete('/app/agents/agent-1/sessions/s-zombie')
    assert resp.status_code == 403
    assert called["runtime"] is False
    assert called["mark"] is False


def test_control_plane_delete_session_quotes_runtime_subpath(monkeypatch):
    from app.main import app
    import app.web as web_module
    user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="public", status="running")
    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(web_module, "AgentRepository", lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: agent))
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)
    seen = {"subpath": None}
    async def _fake_forward_runtime(**kwargs):
        seen["subpath"] = kwargs.get("subpath")
        return 200, b'{"success":true}', "application/json"
    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    monkeypatch.setattr(web_module, "AgentSessionMetadataRepository", lambda _db: SimpleNamespace(mark_deleted=lambda **kwargs: (SimpleNamespace(deleted_at=__import__('datetime').datetime.utcnow()), False)))
    client = TestClient(app)
    resp = client.delete('/app/agents/agent-1/sessions/s%201%25')
    assert resp.status_code == 200
    assert seen["subpath"] == "api/sessions/s%201%25"
