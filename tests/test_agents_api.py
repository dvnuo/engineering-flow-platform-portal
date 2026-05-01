"""Tests for agents API module."""
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from app.models.agent import Agent
from app.schemas.agent import AgentResponse
from app.db import Base
from app.models import User
from sqlalchemy import create_engine
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_agent_model_fields():
    """Test Agent model has expected fields."""
    mapper = inspect(Agent)
    columns = [c.name for c in mapper.columns]
    
    # Check key fields exist
    assert "id" in columns
    assert "name" in columns
    assert "status" in columns
    assert "visibility" in columns
    assert "owner_user_id" in columns
    assert "agent_type" in columns
    assert "capability_profile_id" in columns
    assert "policy_profile_id" in columns
    assert "runtime_profile_id" in columns


def test_agent_response_schema():
    """Test AgentResponse schema fields."""
    fields = AgentResponse.model_fields.keys()
    
    # Check key fields in response
    assert "id" in fields
    assert "name" in fields
    assert "status" in fields
    assert "visibility" in fields
    assert "agent_type" in fields
    assert "capability_profile_id" in fields
    assert "policy_profile_id" in fields
    assert "skill_repo_url" in fields
    assert "skill_branch" in fields


def test_agent_response_normalizes_legacy_repo_url():
    obj = SimpleNamespace(
        id="agent-1",
        name="Agent One",
        status="running",
        visibility="private",
        image="example/image:latest",
        repo_url="git@github.com:Acme/Portal.git",
        branch="main",
        owner_user_id=1,
        cpu="250m",
        memory="512Mi",
        agent_type="workspace",
        capability_profile_id=None,
        policy_profile_id=None,
        disk_size_gi=20,
        description=None,
        last_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    response = AgentResponse.model_validate(obj)
    assert response.repo_url == "https://github.com/Acme/Portal.git"


def test_agent_response_normalizes_skill_repo_url():
    obj = SimpleNamespace(
        id="agent-1",
        name="Agent One",
        status="running",
        visibility="private",
        image="example/image:latest",
        repo_url="https://github.com/Acme/Portal.git",
        branch="main",
        skill_repo_url="git@github.com:Acme/Skills.git",
        skill_branch="feature/skills",
        owner_user_id=1,
        cpu="250m",
        memory="512Mi",
        agent_type="workspace",
        capability_profile_id=None,
        policy_profile_id=None,
        disk_size_gi=20,
        description=None,
        last_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    response = AgentResponse.model_validate(obj)
    assert response.skill_repo_url == "https://github.com/Acme/Skills.git"


def test_agent_status_values():
    """Test valid Agent status values from state machine."""
    from app.utils.state_machine import VALID_STATUSES
    
    # Check that valid statuses are defined
    assert "running" in VALID_STATUSES
    assert "stopped" in VALID_STATUSES
    assert "creating" in VALID_STATUSES


def _build_agents_client_with_overrides():
    from app.main import app
    import app.api.agents as agents_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(username="owner", password_hash="test", role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    state = {"user": SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)}

    def _override_user():
        return state["user"]

    def _override_db():
        yield db

    app.dependency_overrides[agents_api.get_current_user] = _override_user
    app.dependency_overrides[agents_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, _cleanup


def test_invalid_agent_type_on_create_returns_422(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        response = client.post(
            "/api/agents",
            json={"name": "bad-agent", "image": "example/image:latest", "agent_type": "invalid"},
        )
        assert response.status_code == 422
    finally:
        cleanup()


def test_invalid_agent_type_on_update_returns_422(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        create_resp = client.post(
            "/api/agents",
            json={"name": "good-agent", "image": "example/image:latest", "agent_type": "workspace"},
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]
        patch_resp = client.patch(f"/api/agents/{agent_id}", json={"agent_type": "bad-type"})
        assert patch_resp.status_code == 422
    finally:
        cleanup()


def test_create_agent_applies_backend_defaults_when_fields_omitted(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_agent_image_repo", "ghcr.io/acme/portal-agent")
        monkeypatch.setattr(agents_api.settings, "default_agent_image_tag", "v2.4.1")
        monkeypatch.setattr(agents_api.settings, "default_agent_repo_url", "git@github.com:Acme/Portal.git")
        monkeypatch.setattr(agents_api.settings, "default_agent_branch", "release/default")
        monkeypatch.setattr(agents_api.settings, "default_agent_runtime_repo_url", "")
        monkeypatch.setattr(agents_api.settings, "default_agent_runtime_branch", "")
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: SimpleNamespace(status="running", message=None),
        )

        response = client.post("/api/agents", json={"name": "defaulted-agent"})
        assert response.status_code == 200
        body = response.json()
        assert body["image"] == "ghcr.io/acme/portal-agent:v2.4.1"
        assert body["branch"] == "release/default"
        assert body["repo_url"] == "https://github.com/Acme/Portal.git"
    finally:
        cleanup()


def test_defaults_return_runtime_and_skill_defaults(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_skill_repo_url", "https://github.com/acme/skills-default.git")
        monkeypatch.setattr(agents_api.settings, "default_skill_branch", "skills-main")
        response = client.get("/api/agents/defaults")
        assert response.status_code == 200
        body = response.json()
        assert "default_runtime_repo_url" in body
        assert "default_runtime_branch" in body
        assert "default_skill_repo_url" in body
        assert "default_skill_branch" in body
        assert body["default_repo_url"] == body["default_skill_repo_url"]
        assert body["default_branch"] == body["default_skill_branch"]
    finally:
        cleanup()


def test_create_agent_uses_config_runtime_and_payload_skill_repo(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api
        monkeypatch.setattr(agents_api.settings, "default_agent_runtime_repo_url", "https://github.com/acme/runtime.git")
        monkeypatch.setattr(agents_api.settings, "default_agent_runtime_branch", "runtime-branch")
        monkeypatch.setattr(agents_api.settings, "default_skill_repo_url", "https://github.com/acme/default-skills.git")
        monkeypatch.setattr(agents_api.settings, "default_skill_branch", "skills-main")
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        response = client.post("/api/agents", json={"name": "agent", "repo_url": "https://github.com/user/should-be-ignored.git", "branch": "ignored-branch", "skill_repo_url": "git@github.com:Acme/Skills.git", "skill_branch": "feature/skills"})
        assert response.status_code == 200
        body = response.json()
        assert body["repo_url"] == "https://github.com/acme/runtime.git"
        assert body["branch"] == "runtime-branch"
        assert body["skill_repo_url"] == "https://github.com/Acme/Skills.git"
        assert body["skill_branch"] == "feature/skills"
    finally:
        cleanup()


def test_null_agent_type_on_update_returns_422_and_does_not_mutate_agent(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        create_resp = client.post(
            "/api/agents",
            json={"name": "workspace-agent", "image": "example/image:latest", "agent_type": "workspace"},
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]

        patch_resp = client.patch(f"/api/agents/{agent_id}", json={"agent_type": None})
        assert patch_resp.status_code == 422
        assert patch_resp.json()["detail"] == "agent_type cannot be null"

        get_resp = client.get(f"/api/agents/{agent_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["agent_type"] == "workspace"
    finally:
        cleanup()


def test_agent_response_schema_includes_runtime_profile_id():
    fields = AgentResponse.model_fields.keys()
    assert "runtime_profile_id" in fields


def test_create_and_update_agent_runtime_profile_validation_and_response(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        create_missing = client.post(
            "/api/agents",
            json={"name": "bad", "image": "example/image:latest", "runtime_profile_id": "missing-rp"},
        )
        assert create_missing.status_code == 404

        from app.models.runtime_profile import RuntimeProfile

        rp = RuntimeProfile(owner_user_id=1, name="rp", config_json="{}", revision=1, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)

        create_ok = client.post(
            "/api/agents",
            json={"name": "ok", "image": "example/image:latest", "runtime_profile_id": rp.id},
        )
        assert create_ok.status_code == 200
        agent = create_ok.json()
        assert agent["runtime_profile_id"] == rp.id

        patch_missing = client.patch(f"/api/agents/{agent['id']}", json={"runtime_profile_id": "missing-rp"})
        assert patch_missing.status_code == 404
    finally:
        cleanup()


def test_create_agent_without_runtime_profile_id_uses_user_default_profile(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        from app.models.runtime_profile import RuntimeProfile

        default_profile = RuntimeProfile(owner_user_id=1, name="Default", config_json="{}", revision=1, is_default=True)
        db.add(default_profile)
        db.commit()
        db.refresh(default_profile)

        create_ok = client.post("/api/agents", json={"name": "auto-default", "image": "example/image:latest"})
        assert create_ok.status_code == 200
        assert create_ok.json()["runtime_profile_id"] == default_profile.id
    finally:
        cleanup()


def test_create_agent_with_other_users_runtime_profile_is_404(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        from app.models.runtime_profile import RuntimeProfile
        from app.models import User

        foreign_user = User(username="foreign", password_hash="test", role="user", is_active=True)
        db.add(foreign_user)
        db.commit()
        db.refresh(foreign_user)
        foreign_profile = RuntimeProfile(owner_user_id=foreign_user.id, name="Foreign", config_json="{}", revision=1, is_default=True)
        db.add(foreign_profile)
        db.commit()
        db.refresh(foreign_profile)

        resp = client.post(
            "/api/agents",
            json={"name": "bad-foreign", "image": "example/image:latest", "runtime_profile_id": foreign_profile.id},
        )
        assert resp.status_code == 404
    finally:
        cleanup()


def test_agent_chat_model_profile_endpoint_returns_safe_summary(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        from app.models.runtime_profile import RuntimeProfile

        profile = RuntimeProfile(
            owner_user_id=1,
            name="profile-with-secrets",
            revision=7,
            is_default=True,
            config_json='{"llm":{"provider":"claude","model":"claude-sonnet-4-20250514"},"github":{"token":"ghp_secret"},"proxy":{"password":"secret"}}',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

        create_resp = client.post(
            "/api/agents",
            json={"name": "summary-agent", "image": "example/image:latest", "runtime_profile_id": profile.id},
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]

        resp = client.get(f"/api/agents/{agent_id}/chat-model-profile")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "runtime_profile_id": profile.id,
            "revision": 7,
            "provider": "anthropic",
            "current_model": "claude-sonnet-4-20250514",
        }
        assert "config_json" not in body
        assert "token" not in body
        assert "password" not in body
    finally:
        cleanup()


def test_agent_chat_model_profile_endpoint_returns_empty_when_profile_missing(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        with_profile = client.post(
            "/api/agents",
            json={"name": "missing-profile-agent", "image": "example/image:latest"},
        )
        assert with_profile.status_code == 200
        agent_id = with_profile.json()["id"]

        from app.models.agent import Agent

        agent_row = db.query(Agent).filter(Agent.id == agent_id).one()
        agent_row.runtime_profile_id = None
        db.add(agent_row)
        db.commit()

        resp_none = client.get(f"/api/agents/{agent_id}/chat-model-profile")
        assert resp_none.status_code == 200
        assert resp_none.json() == {
            "runtime_profile_id": None,
            "revision": None,
            "provider": "",
            "current_model": "",
        }

        agent_row.runtime_profile_id = "missing-profile-id"
        db.add(agent_row)
        db.commit()

        resp_missing = client.get(f"/api/agents/{agent_id}/chat-model-profile")
        assert resp_missing.status_code == 200
        assert resp_missing.json() == {
            "runtime_profile_id": None,
            "revision": None,
            "provider": "",
            "current_model": "",
        }
    finally:
        cleanup()


def test_agent_chat_model_profile_does_not_infer_provider_model_from_defaults(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        from app.models.runtime_profile import RuntimeProfile

        profile = RuntimeProfile(
            owner_user_id=1,
            name="sparse-profile",
            revision=2,
            is_default=False,
            config_json='{"llm":{"temperature":0.4}}',
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

        create_resp = client.post(
            "/api/agents",
            json={"name": "sparse-model-agent", "image": "example/image:latest", "runtime_profile_id": profile.id},
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]

        resp = client.get(f"/api/agents/{agent_id}/chat-model-profile")
        assert resp.status_code == 200
        assert resp.json() == {
            "runtime_profile_id": profile.id,
            "revision": 2,
            "provider": "",
            "current_model": "",
        }
    finally:
        cleanup()


def test_update_running_agent_runtime_profile_pushes_apply_and_clear(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        from app.models.runtime_profile import RuntimeProfile

        rp = RuntimeProfile(owner_user_id=1, name="rp-sync", config_json='{"llm": {"provider": "openai"}}', revision=3, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)

        create_ok = client.post(
            "/api/agents",
            json={"name": "sync-agent", "image": "example/image:latest"},
        )
        assert create_ok.status_code == 200
        agent_id = create_ok.json()["id"]

        pushed = []

        async def _fake_push(agent, payload):
            pushed.append(payload)
            return True

        monkeypatch.setattr("app.api.agents.runtime_profile_sync_service.push_payload_to_agent", _fake_push)

        apply_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": rp.id})
        assert apply_resp.status_code == 200
        assert pushed[-1]["runtime_profile_id"] == rp.id
        assert pushed[-1]["revision"] == 3

        clear_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": None})
        assert clear_resp.status_code == 422
        assert "runtime_profile_id cannot be null" in clear_resp.json()["detail"]
        assert pushed[-1]["runtime_profile_id"] == rp.id
    finally:
        cleanup()


def test_update_running_agent_runtime_profile_push_failure_still_returns_200(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        from app.models.runtime_profile import RuntimeProfile

        rp = RuntimeProfile(owner_user_id=1, name="rp-sync-fail", config_json='{"llm": {"provider": "openai"}}', revision=3, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)

        create_ok = client.post(
            "/api/agents",
            json={"name": "sync-fail-agent", "image": "example/image:latest"},
        )
        assert create_ok.status_code == 200
        agent_id = create_ok.json()["id"]

        pushed = []

        async def _fake_push(agent, payload):
            pushed.append((agent.id, payload))
            return False

        monkeypatch.setattr("app.api.agents.runtime_profile_sync_service.push_payload_to_agent", _fake_push)

        patch_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": rp.id})
        assert patch_resp.status_code == 200
        assert pushed
    finally:
        cleanup()

def test_patch_skill_repo_triggers_k8s_update(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        calls = {"n": 0}
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        def _upd(_agent):
            calls["n"] += 1
            return SimpleNamespace(status="running", message=None)
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", _upd)
        create = client.post("/api/agents", json={"name": "agent"}).json()
        resp = client.patch(f"/api/agents/{create['id']}", json={"skill_repo_url": "git@github.com:Acme/Skills.git", "skill_branch": "next"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["skill_repo_url"] == "https://github.com/Acme/Skills.git"
        assert body["skill_branch"] == "next"
        assert calls["n"] == 1
    finally:
        cleanup()


def test_patch_legacy_repo_branch_is_ignored_and_does_not_trigger_k8s(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        calls = {"n": 0}
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", lambda _agent: calls.__setitem__("n", calls["n"] + 1) or SimpleNamespace(status="running", message=None))
        create = client.post("/api/agents", json={"name": "agent"}).json()
        before = client.get(f"/api/agents/{create['id']}").json()
        resp = client.patch(f"/api/agents/{create['id']}", json={"repo_url": "https://github.com/user/no.git", "branch": "bad"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["repo_url"] == before["repo_url"]
        assert body["branch"] == before["branch"]
        assert body.get("skill_repo_url") == before.get("skill_repo_url")
        assert body.get("skill_branch") == before.get("skill_branch")
        assert calls["n"] == 0
    finally:
        cleanup()

def test_agent_response_includes_effective_skill_defaults(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api
        monkeypatch.setattr(agents_api.settings, "default_skill_repo_url", "https://github.com/acme/default-skills.git")
        monkeypatch.setattr(agents_api.settings, "default_skill_branch", "skills-main")
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        created = client.post("/api/agents", json={"name": "legacy-like", "skill_repo_url": None, "skill_branch": None})
        assert created.status_code == 200
        agent_id = created.json()["id"]

        # emulate old record with null skill fields
        agent = db.get(Agent, agent_id)
        agent.skill_repo_url = None
        agent.skill_branch = None
        db.add(agent)
        db.commit()

        response = client.get(f"/api/agents/{agent_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["skill_repo_url"] is None
        assert body["skill_branch"] is None
        assert body["effective_skill_repo_url"] == "https://github.com/acme/default-skills.git"
        assert body["effective_skill_branch"] == "skills-main"
    finally:
        cleanup()
