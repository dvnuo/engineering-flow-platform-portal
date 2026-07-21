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
    assert "runtime_profile_id" in columns
    assert "runtime_type" in columns
    assert "agent_settings_repo_url" in columns
    assert "agent_settings_branch" in columns
    assert "agent_settings_subdir" in columns


def test_agent_response_schema():
    """Test AgentResponse schema fields."""
    fields = AgentResponse.model_fields.keys()
    
    # Check key fields in response
    assert "id" in fields
    assert "name" in fields
    assert "status" in fields
    assert "visibility" in fields
    assert "agent_type" in fields
    assert "skill_repo_url" in fields
    assert "skill_branch" in fields
    assert "agent_settings_repo_url" in fields
    assert "agent_settings_branch" in fields
    assert "agent_settings_subdir" in fields
    assert "effective_agent_settings_repo_url" in fields
    assert "effective_agent_settings_branch" in fields
    assert "effective_agent_settings_subdir" in fields
    assert "runtime_type" in fields


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
        disk_size_gi=20,
        description=None,
        last_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    response = AgentResponse.model_validate(obj)
    assert response.skill_repo_url == "https://github.com/Acme/Skills.git"


def test_agent_response_normalizes_agent_settings_repo_url():
    obj = SimpleNamespace(
        id="agent-1",
        name="Agent One",
        status="running",
        visibility="private",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        agent_settings_repo_url="git@github.com:Acme/Agents.git",
        agent_settings_branch="feature/persona",
        agent_settings_subdir="profiles/default",
        owner_user_id=1,
        cpu="250m",
        memory="512Mi",
        agent_type="workspace",
        disk_size_gi=20,
        description=None,
        last_error=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    response = AgentResponse.model_validate(obj)
    assert response.agent_settings_repo_url == "https://github.com/Acme/Agents.git"


def test_agent_status_values():
    """Test valid Agent status values from state machine."""
    from app.utils.state_machine import VALID_STATUSES
    
    # Check that valid statuses are defined
    assert "running" in VALID_STATUSES
    assert "stopped" in VALID_STATUSES
    assert "creating" in VALID_STATUSES
    assert "restarting" in VALID_STATUSES


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
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="stopped", message=None))
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
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: SimpleNamespace(status="running", message=None),
        )

        response = client.post("/api/agents", json={"name": "defaulted-agent"})
        assert response.status_code == 200
        body = response.json()
        assert body["runtime_type"] == "native"
        assert body["image"] == "ghcr.io/acme/portal-agent:v2.4.1"
        assert body["branch"] is None
        assert body["repo_url"] is None
    finally:
        cleanup()


def test_create_agent_uses_default_runtime_type_setting_when_omitted(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_runtime_type", "opencode")
        monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_repo", "ghcr.io/acme/opencode")
        monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_tag", "v3")
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: SimpleNamespace(status="running", message=None),
        )

        response = client.post("/api/agents", json={"name": "default-opencode-agent"})

        assert response.status_code == 200
        body = response.json()
        assert body["runtime_type"] == "opencode"
        assert body["image"] == "ghcr.io/acme/opencode:v3"
    finally:
        cleanup()


def test_create_agent_ensures_profile_secret_before_deployment(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        order = []
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: order.append("create_runtime") or SimpleNamespace(status="running", message=None),
        )
        monkeypatch.setattr(
            "app.api.agents.runtime_profile_secret_service.ensure_none_secret",
            lambda: order.append("ensure_none_secret"),
        )
        monkeypatch.setattr(
            "app.api.agents.runtime_profile_secret_service.sync_profile_secret",
            lambda _profile: order.append("sync_profile_secret"),
        )

        response = client.post("/api/agents", json={"name": "secret-first-create"})
        assert response.status_code == 200
        # Secrets must exist before the Deployment so EFP_PROFILE_CONFIG
        # secretKeyRef resolves on first pod start.
        assert order == ["ensure_none_secret", "sync_profile_secret", "create_runtime"]
    finally:
        cleanup()


def test_create_agent_secret_ensure_failure_still_returns_created_agent(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: SimpleNamespace(status="creating", message=None),
        )

        def _boom(*_args, **_kwargs):
            raise RuntimeError("secret upsert failed")

        monkeypatch.setattr(
            "app.api.agents.runtime_profile_secret_service.sync_profile_secret",
            _boom,
        )

        response = client.post(
            "/api/agents",
            json={"name": "secret-failure-create"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "secret-failure-create"
        assert body["status"] == "creating"
    finally:
        cleanup()


def test_defaults_return_runtime_and_skill_defaults(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_agent_settings_repo_url", "https://github.com/acme/agents-default.git")
        monkeypatch.setattr(agents_api.settings, "default_agent_settings_branch", "agents-main")
        monkeypatch.setattr(agents_api.settings, "default_agent_settings_repo_subdir", "profiles/default")
        monkeypatch.setattr(agents_api.settings, "default_skill_repo_url", "https://github.com/acme/skills-default.git")
        monkeypatch.setattr(agents_api.settings, "default_skill_branch", "skills-main")
        response = client.get("/api/agents/defaults")
        assert response.status_code == 200
        body = response.json()
        assert "default_runtime_repo_url" not in body
        assert "default_runtime_branch" not in body
        assert "runtime_repo_url" not in body
        assert "runtime_branch" not in body
        assert body["default_agent_settings_repo_url"] == "https://github.com/acme/agents-default.git"
        assert body["default_agent_settings_branch"] == "agents-main"
        assert body["default_agent_settings_repo_subdir"] == "profiles/default"
        assert "default_skill_repo_url" in body
        assert "default_skill_branch" in body
        assert body["default_runtime_type"] == "native"
        assert [item["value"] for item in body["runtime_types"]] == ["native", "opencode"]
        native_runtime = next(item for item in body["runtime_types"] if item["value"] == "native")
        opencode_runtime = next(item for item in body["runtime_types"] if item["value"] == "opencode")
        assert native_runtime["label"] == "EFP Native Runtime"
        assert native_runtime["default_mount_path"] == "/workspace"
        assert opencode_runtime["label"] == "OpenCode Runtime"
        assert opencode_runtime["image_repo"] == "ghcr.io/dvnuo/efp-opencode-runtime"
        assert opencode_runtime["image_tag"] == "1.14.39"
        assert opencode_runtime["default_mount_path"] == "/workspace"
        assert "enable_runtime_source_overlay" not in body
        assert body["mount_path"] == "/workspace"
        assert body["default_repo_url"] == body["default_skill_repo_url"]
        assert body["default_branch"] == body["default_skill_branch"]
    finally:
        cleanup()


def test_defaults_invalid_runtime_type_falls_back_to_native(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_runtime_type", "bad")
        response = client.get("/api/agents/defaults")
        assert response.status_code == 200
        body = response.json()
        assert body["default_runtime_type"] == "native"
        assert {item["value"] for item in body["runtime_types"]} == {"native", "opencode"}
    finally:
        cleanup()


def test_create_agent_uses_config_runtime_and_payload_skill_repo(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api
        monkeypatch.setattr(agents_api.settings, "default_skill_repo_url", "https://github.com/acme/default-skills.git")
        monkeypatch.setattr(agents_api.settings, "default_skill_branch", "skills-main")
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        response = client.post("/api/agents", json={"name": "agent", "repo_url": "https://github.com/user/should-be-ignored.git", "branch": "ignored-branch", "skill_repo_url": "git@github.com:Acme/Skills.git", "skill_branch": "feature/skills"})
        assert response.status_code == 200
        body = response.json()
        assert body["repo_url"] is None
        assert body["branch"] is None
        assert body["skill_repo_url"] == "https://github.com/Acme/Skills.git"
        assert body["skill_branch"] == "feature/skills"
    finally:
        cleanup()


def test_create_agent_uses_config_and_payload_agent_settings_repo(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_agent_settings_repo_url", "https://github.com/acme/default-agents.git")
        monkeypatch.setattr(agents_api.settings, "default_agent_settings_branch", "agents-main")
        monkeypatch.setattr(agents_api.settings, "default_agent_settings_repo_subdir", "")
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

        default_response = client.post("/api/agents", json={"name": "default-agent-settings"})
        assert default_response.status_code == 200
        default_body = default_response.json()
        assert default_body["agent_settings_repo_url"] == "https://github.com/acme/default-agents.git"
        assert default_body["agent_settings_branch"] == "agents-main"
        assert default_body["agent_settings_subdir"] is None

        response = client.post(
            "/api/agents",
            json={
                "name": "custom-agent-settings",
                "agent_settings_repo_url": "git@github.com:Acme/Agents.git",
                "agent_settings_branch": "feature/persona",
                "agent_settings_subdir": "profiles/default",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["agent_settings_repo_url"] == "https://github.com/Acme/Agents.git"
        assert body["agent_settings_branch"] == "feature/persona"
        assert body["agent_settings_subdir"] == "profiles/default"
        assert body["effective_agent_settings_repo_url"] == "https://github.com/Acme/Agents.git"
        assert body["effective_agent_settings_branch"] == "feature/persona"
        assert body["effective_agent_settings_subdir"] == "profiles/default"
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
            config_json='{"llm":{"provider":"github_copilot","model":"gpt-5.5"},"github":{"token":"ghp_secret"},"proxy":{"password":"secret"}}',
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
            "provider": "github_copilot",
            "current_model": "gpt-5.5",
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


def test_update_runtime_profile_id_ensures_secret_and_rolls_deployment(monkeypatch):
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

        calls = {"secret": [], "update_runtime": 0}

        def _sync_secret(profile):
            calls["secret"].append(profile.id)

        def _update_runtime(_agent):
            calls["update_runtime"] += 1
            return SimpleNamespace(status="running", message=None)

        monkeypatch.setattr("app.api.agents.runtime_profile_secret_service.sync_profile_secret", _sync_secret)
        monkeypatch.setattr("app.api.agents.runtime_profile_secret_service.ensure_none_secret", lambda: None)
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", _update_runtime)

        apply_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": rp.id})
        assert apply_resp.status_code == 200
        # Rebind ensures the target Secret exists and rolls the deployment so
        # the pod env secretKeyRef points at the new profile Secret.
        assert calls["secret"] == [rp.id]
        assert calls["update_runtime"] == 1

        clear_resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": None})
        assert clear_resp.status_code == 422
        assert "runtime_profile_id cannot be null" in clear_resp.json()["detail"]
    finally:
        cleanup()


def test_restart_ensures_profile_secret_and_restarts(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.start_agent", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.enabled", True)
        calls = {"secret": 0, "restart": 0}

        def _stop_agent(_agent):
            raise AssertionError("restart endpoint must not stop the agent")

        def _restart_agent(agent):
            calls["restart"] += 1
            assert agent.status == "running"
            return SimpleNamespace(status="restarting", message="Restart requested: req-1")

        monkeypatch.setattr("app.api.agents.k8s_service.stop_agent", _stop_agent)
        monkeypatch.setattr("app.api.agents.k8s_service.restart_agent", _restart_agent)
        monkeypatch.setattr(
            "app.api.agents.runtime_profile_secret_service.sync_profile_secret",
            lambda _profile: calls.__setitem__("secret", calls["secret"] + 1),
        )
        monkeypatch.setattr("app.api.agents.runtime_profile_secret_service.ensure_none_secret", lambda: None)

        create_ok = client.post("/api/agents", json={"name": "sync-fail-agent", "image": "example/image:latest"})
        agent_id = create_ok.json()["id"]
        restart_resp = client.post(f"/api/agents/{agent_id}/restart")
        assert restart_resp.status_code == 200
        assert restart_resp.json()["status"] == "restarting"
        persisted = db.get(Agent, agent_id)
        assert persisted.status == "restarting"
        assert persisted.last_error == "Restart requested: req-1"
        assert calls["restart"] == 1
        assert calls["secret"] >= 1
    finally:
        cleanup()


def test_restart_allows_stopped_and_failed_agents(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.enabled", True)
        restarted_from = []

        def _restart_agent(agent):
            restarted_from.append(agent.status)
            return SimpleNamespace(status="restarting", message=f"Restart requested from {agent.status}")

        monkeypatch.setattr("app.api.agents.k8s_service.restart_agent", _restart_agent)

        create_ok = client.post("/api/agents", json={"name": "restart-allowed-agent", "image": "example/image:latest"})
        agent_id = create_ok.json()["id"]

        for restartable_status in ["stopped", "failed"]:
            agent = db.get(Agent, agent_id)
            agent.status = restartable_status
            agent.last_error = None
            db.add(agent)
            db.commit()

            restart_resp = client.post(f"/api/agents/{agent_id}/restart")

            assert restart_resp.status_code == 200
            assert restart_resp.json()["status"] == "restarting"
            assert db.get(Agent, agent_id).status == "restarting"

        assert restarted_from == ["stopped", "failed"]
    finally:
        cleanup()


def test_restart_returns_502_without_marking_agent_failed_when_runtime_restart_fails(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        message = "Deployment patch failed"

        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.enabled", True)
        monkeypatch.setattr("app.api.agents.k8s_service.restart_agent", lambda _agent: SimpleNamespace(status="failed", message=message))

        create_ok = client.post("/api/agents", json={"name": "restart-fail-agent", "image": "example/image:latest"})
        agent_id = create_ok.json()["id"]

        restart_resp = client.post(f"/api/agents/{agent_id}/restart")

        assert restart_resp.status_code == 502
        assert restart_resp.json()["detail"] == message
        persisted = db.get(Agent, agent_id)
        assert persisted.status == "running"
        assert persisted.last_error == message
    finally:
        cleanup()


def test_restart_rejects_in_progress_statuses(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.enabled", True)

        def _unexpected_restart(_agent):
            raise AssertionError("in-progress agents must not restart again")

        monkeypatch.setattr("app.api.agents.k8s_service.restart_agent", _unexpected_restart)

        create_ok = client.post("/api/agents", json={"name": "restart-guard-agent", "image": "example/image:latest"})
        agent_id = create_ok.json()["id"]

        for blocked_status in ["creating", "restarting", "deleting"]:
            agent = db.get(Agent, agent_id)
            agent.status = blocked_status
            agent.last_error = None
            db.add(agent)
            db.commit()

            restart_resp = client.post(f"/api/agents/{agent_id}/restart")

            assert restart_resp.status_code == 409
            assert f"Cannot restart agent from status '{blocked_status}'" in restart_resp.json()["detail"]
            assert db.get(Agent, agent_id).status == blocked_status
    finally:
        cleanup()


def test_restart_returns_409_when_k8s_disabled_without_marking_agent_running_or_failed(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        calls = {"restart": 0}
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.enabled", False)

        def _restart_agent(_agent):
            calls["restart"] += 1
            return SimpleNamespace(status="restarting", message="should not be called")

        monkeypatch.setattr("app.api.agents.k8s_service.restart_agent", _restart_agent)

        create_ok = client.post("/api/agents", json={"name": "noop-restart-agent", "image": "example/image:latest"})
        agent_id = create_ok.json()["id"]

        restart_resp = client.post(f"/api/agents/{agent_id}/restart")

        assert restart_resp.status_code == 409
        assert "restart is unsupported" in restart_resp.json()["detail"]
        persisted = db.get(Agent, agent_id)
        assert persisted.status == "running"
        assert persisted.last_error is None
        assert calls["restart"] == 0
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


def test_patch_same_skill_repo_branch_sets_rollout_marker(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: SimpleNamespace(status="running", message=None),
        )

        captured = {"calls": 0, "marker": None}

        def _update(agent):
            captured["calls"] += 1
            captured["marker"] = getattr(agent, "skill_asset_version", None)
            return SimpleNamespace(status="running", message=None)

        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", _update)

        create_resp = client.post(
            "/api/agents",
            json={
                "name": "skill-agent",
                "skill_repo_url": "https://github.com/acme/skills.git",
                "skill_branch": "main",
            },
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]

        patch_resp = client.patch(
            f"/api/agents/{agent_id}",
            json={
                "skill_repo_url": "https://github.com/acme/skills.git",
                "skill_branch": "main",
            },
        )

        assert patch_resp.status_code == 200
        assert captured["calls"] == 1
        assert captured["marker"]
        assert captured["marker"].startswith("agent-skill-save-")
    finally:
        cleanup()


def test_patch_same_agent_settings_repo_branch_sets_rollout_marker(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: SimpleNamespace(status="running", message=None),
        )

        captured = {"calls": 0, "marker": None}

        def _update(agent):
            captured["calls"] += 1
            captured["marker"] = getattr(agent, "agent_settings_asset_version", None)
            return SimpleNamespace(status="running", message=None)

        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", _update)

        create_resp = client.post(
            "/api/agents",
            json={
                "name": "settings-agent",
                "agent_settings_repo_url": "https://github.com/acme/agents.git",
                "agent_settings_branch": "main",
                "agent_settings_subdir": "profiles/default",
            },
        )
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]

        patch_resp = client.patch(
            f"/api/agents/{agent_id}",
            json={
                "agent_settings_repo_url": "https://github.com/acme/agents.git",
                "agent_settings_branch": "main",
                "agent_settings_subdir": "profiles/default",
            },
        )

        assert patch_resp.status_code == 200
        assert captured["calls"] == 1
        assert captured["marker"]
        assert captured["marker"].startswith("agent-settings-save-")
    finally:
        cleanup()


def test_patch_name_only_does_not_set_skill_rollout_marker(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr(
            "app.api.agents.k8s_service.create_agent_runtime",
            lambda _agent: SimpleNamespace(status="running", message=None),
        )

        def _unexpected_uuid4():
            raise AssertionError("name-only PATCH should not create a skill rollout marker")

        def _unexpected_update(_agent):
            raise AssertionError("name-only PATCH should not update Kubernetes runtime")

        monkeypatch.setattr("app.api.agents.uuid4", _unexpected_uuid4)
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", _unexpected_update)

        create_resp = client.post("/api/agents", json={"name": "rename-agent"})
        assert create_resp.status_code == 200
        agent_id = create_resp.json()["id"]

        patch_resp = client.patch(f"/api/agents/{agent_id}", json={"name": "renamed-agent"})

        assert patch_resp.status_code == 200
        assert patch_resp.json()["name"] == "renamed-agent"
    finally:
        cleanup()


def test_patch_runtime_profile_update_ensures_target_secret(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        from app.models.runtime_profile import RuntimeProfile

        rp = RuntimeProfile(owner_user_id=1, name="rp-agent-aware", config_json='{"llm": {"provider": "openai"}}', revision=1, is_default=True)
        db.add(rp)
        db.commit()
        db.refresh(rp)
        agent_id = client.post("/api/agents", json={"name": "sync-agent-aware", "image": "example/image:latest"}).json()["id"]

        calls = {"secret": []}

        def _sync_secret(profile):
            calls["secret"].append(profile.id)

        monkeypatch.setattr("app.api.agents.runtime_profile_secret_service.sync_profile_secret", _sync_secret)
        monkeypatch.setattr("app.api.agents.runtime_profile_secret_service.ensure_none_secret", lambda: None)
        resp = client.patch(f"/api/agents/{agent_id}", json={"runtime_profile_id": rp.id})
        assert resp.status_code == 200
        assert calls["secret"] == [rp.id]
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


def test_create_agent_accepts_opencode_runtime_choice(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_repo", "ghcr.io/acme/opencode")
        monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_tag", "v1")
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        response = client.post("/api/agents", json={"name": "legacy-agent", "runtime_type": "opencode"})
        assert response.status_code == 200
        body = response.json()
        assert body["runtime_type"] == "opencode"
        assert body["image"] == "ghcr.io/acme/opencode:v1"
    finally:
        cleanup()

def test_patch_runtime_type_native_marker_is_ignored(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        calls = {"n": 0}
        monkeypatch.setattr(
            "app.api.agents.k8s_service.update_agent_runtime",
            lambda _agent: calls.__setitem__("n", calls["n"] + 1) or SimpleNamespace(status="running", message=None),
        )
        created = client.post("/api/agents", json={"name": "agent"}).json()
        resp = client.patch(f"/api/agents/{created['id']}", json={"runtime_type": "native"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_type"] == "native"
        assert calls["n"] == 0
    finally:
        cleanup()


def test_patch_runtime_type_opencode_choice_updates_image(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        import app.api.agents as agents_api

        monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_repo", "ghcr.io/acme/opencode")
        monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_tag", "v2")
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        updates = []
        monkeypatch.setattr(
            "app.api.agents.k8s_service.update_agent_runtime",
            lambda agent: updates.append((agent.runtime_type, agent.image, agent.mount_path)) or SimpleNamespace(status="running", message=None),
        )
        created = client.post("/api/agents", json={"name": "agent"}).json()
        resp = client.patch(f"/api/agents/{created['id']}", json={"runtime_type": "opencode"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["runtime_type"] == "opencode"
        assert body["image"] == "ghcr.io/acme/opencode:v2"
        assert updates == [("opencode", "ghcr.io/acme/opencode:v2", "/workspace")]
    finally:
        cleanup()


def test_patch_invalid_runtime_type_returns_422(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        created = client.post("/api/agents", json={"name": "agent"}).json()
        resp = client.patch(f"/api/agents/{created['id']}", json={"runtime_type": "bad"})
        assert resp.status_code == 422
    finally:
        cleanup()


def test_patch_null_runtime_type_returns_422_and_does_not_mutate_agent(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        created = client.post("/api/agents", json={"name": "agent"}).json()
        resp = client.patch(f"/api/agents/{created['id']}", json={"runtime_type": None})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "runtime_type cannot be null"
        after = client.get(f"/api/agents/{created['id']}").json()
        assert after["runtime_type"] == "native"
    finally:
        cleanup()


def test_create_agent_backend_defaults_mount_path_to_workspace(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        response = client.post("/api/agents", json={"name": "workspace-agent"})
        assert response.status_code == 200
        agent_id = response.json()["id"]
        agent = db.get(Agent, agent_id)
        assert agent.mount_path == "/workspace"
    finally:
        cleanup()


def test_patch_runtime_type_native_marker_does_not_rewrite_mount_path(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        calls = {"n": 0}
        monkeypatch.setattr(
            "app.api.agents.k8s_service.update_agent_runtime",
            lambda _agent: calls.__setitem__("n", calls["n"] + 1) or SimpleNamespace(status="running", message=None),
        )
        created = client.post("/api/agents", json={"name": "agent"}).json()
        resp = client.patch(f"/api/agents/{created['id']}", json={"runtime_type": "native"})
        assert resp.status_code == 200
        agent = db.get(Agent, created["id"])
        assert agent.mount_path == "/workspace"
        assert calls["n"] == 0
    finally:
        cleanup()


def test_patch_runtime_type_does_not_override_custom_mount_path(monkeypatch):
    client, db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        monkeypatch.setattr("app.api.agents.k8s_service.update_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        created = client.post("/api/agents", json={"name": "agent", "mount_path": "/custom/workspace"}).json()
        resp = client.patch(f"/api/agents/{created['id']}", json={"runtime_type": "native"})
        assert resp.status_code == 200
        agent = db.get(Agent, created["id"])
        assert agent.mount_path == "/custom/workspace"
    finally:
        cleanup()


def test_create_invalid_runtime_type_returns_422(monkeypatch):
    client, _db, cleanup = _build_agents_client_with_overrides()
    try:
        monkeypatch.setattr("app.api.agents.k8s_service.create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
        resp = client.post("/api/agents", json={"name": "bad", "runtime_type": "bad"})
        assert resp.status_code == 422
    finally:
        cleanup()

def test_agents_api_runtime_overlay_no_default_agent_repo_fallback_source_marker():
    from pathlib import Path
    src = Path('app/api/agents.py').read_text(encoding='utf-8')
    assert 'default_agent_runtime_repo_url' not in src
    assert 'enable_runtime_source_overlay' not in src
