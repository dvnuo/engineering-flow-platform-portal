import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


class _DB:
    def close(self):
        return None


def _setup_thinking_panel_client(monkeypatch, chatlog_payload):
    from app.main import app
    import app.web as web_module

    fake_user = SimpleNamespace(id=11, username="owner", nickname="Owner", role="user")
    fake_agent = SimpleNamespace(id="agent-1", owner_user_id=11, visibility="private", status="running")

    monkeypatch.setattr(web_module, "_current_user_from_cookie", lambda _request: fake_user)
    monkeypatch.setattr(web_module, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(
        web_module,
        "AgentRepository",
        lambda _db: SimpleNamespace(get_by_id=lambda _agent_id: fake_agent),
    )
    monkeypatch.setattr(web_module.settings, "k8s_enabled", True)

    async def _fake_forward_runtime(**_kwargs):
        return 200, json.dumps(chatlog_payload).encode("utf-8"), "application/json"

    monkeypatch.setattr(web_module, "_forward_runtime", _fake_forward_runtime)
    return TestClient(app)


def test_thinking_process_panel_renders_active_skill_from_top_level_skill_session(monkeypatch):
    chatlog = {
        "skill_session": {
            "schema_version": "active_skill_contract.v1",
            "skill_name": "review-pull-request",
            "status": "active",
            "goal": "Review PR #12",
            "turn_count": 2,
            "activation_reason": "continued",
            "skill_hash": "abc123",
            "allowed_tools": ["github_get_pull_request", "github_list_pull_request_files"],
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: review-pull-request" in response.text
    assert "Goal: Review PR #12" in response.text
    assert "Status: active" in response.text
    assert "Turn: 2" in response.text
    assert "Reason: continued" in response.text
    assert "Skill hash: abc123" in response.text
    assert "github_get_pull_request" in response.text


def test_thinking_process_panel_renders_active_skill_from_nested_metadata_session(monkeypatch):
    chatlog = {
        "metadata": {
            "active_skill_session": {
                "schema_version": "active_skill_contract.v1",
                "skill_name": "create-pull-request",
                "status": "active",
                "goal": "Create PR",
                "turn_count": 3,
                "activation_reason": "matched",
                "skill_hash": "def456",
                "allowed_tools": ["github_create_pull_request"],
            }
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: create-pull-request" in response.text
    assert "Goal: Create PR" in response.text
    assert "Turn: 3" in response.text
    assert "Reason: matched" in response.text
    assert "github_create_pull_request" in response.text


def test_thinking_process_panel_renders_active_skill_from_flat_metadata_fields(monkeypatch):
    chatlog = {
        "metadata": {
            "active_skill_name": "triage-incident",
            "active_skill_status": "active",
            "active_skill_goal": "Triage issue #77",
            "active_skill_turn_count": 0,
            "active_skill_activation_reason": "manual",
            "active_skill_hash": "flat999",
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: triage-incident" in response.text
    assert "Goal: Triage issue #77" in response.text
    assert "Turn: 0" in response.text


def test_thinking_process_panel_handles_non_mapping_metadata_and_skill_session(monkeypatch):
    chatlog = {
        "metadata": "bad-metadata",
        "skill_session": "bad-skill-session",
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert ("Thinking Process" in response.text) or ("Active Skill:" not in response.text)


def test_thinking_process_panel_uses_nested_skill_when_top_level_skill_session_is_invalid(monkeypatch):
    chatlog = {
        "skill_session": "bad-skill-session",
        "metadata": {
            "active_skill_session": {
                "schema_version": "active_skill_contract.v1",
                "skill_name": "review-pull-request",
                "status": "active",
                "goal": "Review PR #12",
                "turn_count": 2,
                "activation_reason": "continued",
                "skill_hash": "abc123",
                "allowed_tools": ["github_get_pull_request"],
            }
        },
        "llm_debug": {},
    }
    client = _setup_thinking_panel_client(monkeypatch, chatlog)

    response = client.get("/app/agents/agent-1/thinking/panel?session_id=s-1")

    assert response.status_code == 200
    assert "Active Skill: review-pull-request" in response.text
    assert "Goal: Review PR #12" in response.text
    assert "github_get_pull_request" in response.text
