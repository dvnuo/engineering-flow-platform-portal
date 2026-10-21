"""Run settings chosen in the chat composer live with the session.

Saved through ``PUT /api/agents/{id}/sessions/{sid}/run-settings``, read back
with GET, validated against the agent's current profile like a chat request,
and filled by the chat proxy into a request that names none of its own.
"""

import json
from types import SimpleNamespace

from test_agents_api import _build_agents_client_with_overrides

from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_session_metadata_repo import AgentSessionMetadataRepository
from app.services.session_run_settings_service import (
    apply_session_run_settings,
    load_session_run_settings,
    parse_run_settings_json,
)


def _agent_with_copilot_profile(client, db, monkeypatch, *, model="gpt-6-luna"):
    monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    from app.models.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(
        owner_user_id=1,
        name="run-settings-profile",
        revision=1,
        is_default=True,
        config_json=json.dumps({"llm": {"provider": "github_copilot", "model": model}}),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    created = client.post("/api/agents", json={"name": "run-settings-agent", "image": "example/image:latest"})
    assert created.status_code == 200
    return created.json()["id"]


def test_session_run_settings_round_trip(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        agent_id = _agent_with_copilot_profile(client, db, monkeypatch)
        path = f"/api/agents/{agent_id}/sessions/sess-1/run-settings"

        empty = client.get(path)
        assert empty.status_code == 200
        assert empty.json() == {
            "agent_id": agent_id,
            "session_id": "sess-1",
            "model_override": None,
            "reasoning_effort": None,
            "max_context_tokens": None,
        }

        saved = client.put(path, json={"model_override": "gpt-6-astra", "reasoning_effort": "low", "max_context_tokens": 64000})
        assert saved.status_code == 200
        assert saved.json() == {
            "agent_id": agent_id,
            "session_id": "sess-1",
            "model_override": "gpt-6-astra",
            "reasoning_effort": "low",
            "max_context_tokens": 64000,
        }
        assert client.get(path).json() == saved.json()
        record = AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id=agent_id, session_id="sess-1")
        assert json.loads(record.run_settings_json) == {"max_context_tokens": 64000, "model_override": "gpt-6-astra", "reasoning_effort": "low"}

        # Another session of the same agent has its own settings.
        assert client.get(f"/api/agents/{agent_id}/sessions/sess-2/run-settings").json()["model_override"] is None

        # Partial saves clear what they leave out: back to the model default,
        # keep the thinking level.
        partial = client.put(path, json={"reasoning_effort": "low"})
        assert partial.json()["model_override"] is None
        assert partial.json()["reasoning_effort"] == "low"
        assert partial.json()["max_context_tokens"] is None

        # Back to the agent's defaults altogether: nothing stored.
        cleared = client.put(path, json={"model_override": None, "reasoning_effort": None, "max_context_tokens": None})
        assert cleared.status_code == 200
        assert cleared.json()["reasoning_effort"] is None
        assert load_session_run_settings(db, agent_id, "sess-1") == {}
        assert AgentSessionMetadataRepository(db).get_by_agent_and_session(agent_id=agent_id, session_id="sess-1").run_settings_json is None
    finally:
        cleanup()


def test_session_run_settings_reject_values_the_profile_does_not_offer(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        agent_id = _agent_with_copilot_profile(client, db, monkeypatch)
        path = f"/api/agents/{agent_id}/sessions/sess-1/run-settings"
        for body in (
            {"model_override": "gpt-4"},
            {"reasoning_effort": "ultra"},
            {"max_context_tokens": 123},
        ):
            response = client.put(path, json=body)
            assert response.status_code == 422, body
        assert client.get(path).json()["model_override"] is None
        assert client.put(f"/api/agents/{agent_id}/sessions/%20/run-settings", json={"reasoning_effort": "low"}).status_code == 422
        assert client.get("/api/agents/missing/sessions/sess-1/run-settings").status_code == 404
    finally:
        cleanup()


def test_proxy_fills_the_sessions_settings_only_when_the_request_names_none(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        agent_id = _agent_with_copilot_profile(client, db, monkeypatch)
        agent = AgentRepository(db).get_by_id(agent_id)
        path = f"/api/agents/{agent_id}/sessions/sess-1/run-settings"
        assert client.put(path, json={"model_override": "gpt-6-astra", "reasoning_effort": "low"}).status_code == 200

        filled = apply_session_run_settings(db, agent, {"message": "hi", "session_id": "sess-1"})
        assert filled == {"message": "hi", "session_id": "sess-1", "model_override": "gpt-6-astra", "reasoning_effort": "low"}

        # The request's own choice wins, even when it names only one setting
        # or explicitly asks for the default.
        explicit = {"message": "hi", "session_id": "sess-1", "reasoning_effort": "high"}
        assert apply_session_run_settings(db, agent, explicit) == explicit
        default = {"message": "hi", "session_id": "sess-1", "model_override": None}
        assert apply_session_run_settings(db, agent, default) == default
        # No session, nothing to fill in.
        assert apply_session_run_settings(db, agent, {"message": "hi"}) == {"message": "hi"}

        # Stored values the profile no longer offers are ignored rather than
        # failing the chat.
        AgentSessionMetadataRepository(db).upsert(agent_id=agent_id, session_id="sess-1", run_settings_json=json.dumps({"model_override": "gpt-4"}))
        assert apply_session_run_settings(db, agent, {"message": "hi", "session_id": "sess-1"}) == {"message": "hi", "session_id": "sess-1"}
    finally:
        cleanup()


def test_parse_run_settings_json_keeps_only_usable_values():
    assert parse_run_settings_json(None) == {}
    assert parse_run_settings_json("not json") == {}
    assert parse_run_settings_json("[1]") == {}
    assert parse_run_settings_json(json.dumps({"model_override": " gpt-6-sol ", "reasoning_effort": "LOW", "max_context_tokens": 64000, "other": 1})) == {
        "model_override": "gpt-6-sol",
        "reasoning_effort": "low",
        "max_context_tokens": 64000,
    }
    assert parse_run_settings_json(json.dumps({"model_override": "", "max_context_tokens": True})) == {}
