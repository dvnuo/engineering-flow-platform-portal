import asyncio
from types import SimpleNamespace

from app.api import agents as agents_api
from app.main import app
from app.db import Base
from app.models import Agent, User
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _fake_user():
    return SimpleNamespace(id=1, role="admin")


def _create_payload(name: str = "agent"):
    return agents_api.AgentCreateRequest(name=name)


def _build_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="admin", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return db


def test_t13_create_agent_uses_single_runtime_image_and_workspace(monkeypatch):
    db_session = _build_db()
    monkeypatch.setattr(agents_api, "get_current_user", lambda: _fake_user())
    monkeypatch.setattr(agents_api.settings, "default_agent_image_repo", "ghcr.io/example/efp-runtime")
    monkeypatch.setattr(agents_api.settings, "default_agent_image_tag", "test-runtime")
    monkeypatch.setattr(agents_api.settings, "default_skill_repo_url", "https://example.com/skills.git")

    calls = []
    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda agent: calls.append(agent.id) or SimpleNamespace(status="running", message=None))

    try:
        created = asyncio.run(agents_api.create_agent(_create_payload("single-runtime"), _fake_user(), db_session))

        assert created.runtime_type == "native"
        assert created.image == "ghcr.io/example/efp-runtime:test-runtime"
        created_db = db_session.query(Agent).filter(Agent.id == created.id).one()
        assert created_db.runtime_type == "native"
        assert created_db.mount_path == "/workspace"
        assert created_db.service_name.startswith("agent-")
        assert len(calls) == 1
    finally:
        db_session.close()


def test_t13_edit_runtime_type_native_marker_is_ignored(monkeypatch):
    db_session = _build_db()
    monkeypatch.setattr(agents_api.settings, "default_agent_image_repo", "ghcr.io/example/efp-runtime")
    monkeypatch.setattr(agents_api.settings, "default_agent_image_tag", "test-runtime")
    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    updates = []
    monkeypatch.setattr(agents_api.k8s_service, "update_agent_runtime", lambda agent: updates.append((agent.runtime_type, agent.mount_path, agent.image)) or SimpleNamespace(status="running", message=None))

    try:
        created = asyncio.run(agents_api.create_agent(_create_payload("single-runtime"), _fake_user(), db_session))
        updated = asyncio.run(agents_api.update_agent(created.id, agents_api.AgentUpdateRequest(runtime_type="native"), _fake_user(), db_session))

        assert updated.runtime_type == "native"
        updated_db = db_session.query(Agent).filter(Agent.id == created.id).one()
        assert updated_db.mount_path == "/workspace"
        assert updated_db.image == "ghcr.io/example/efp-runtime:test-runtime"
        assert updates == []
    finally:
        db_session.close()


def test_t13_skill_repo_changes_trigger_runtime_rollout_without_tool_repo(monkeypatch):
    db_session = _build_db()
    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    seen = {}
    monkeypatch.setattr(agents_api.k8s_service, "update_agent_runtime", lambda agent: seen.update(skill_repo_url=agent.skill_repo_url) or SimpleNamespace(status="running", message=None))

    try:
        created = asyncio.run(agents_api.create_agent(_create_payload("skills-update"), _fake_user(), db_session))
        updated = asyncio.run(
            agents_api.update_agent(
                created.id,
                agents_api.AgentUpdateRequest(skill_repo_url="https://example.com/new-skills.git"),
                _fake_user(),
                db_session,
            )
        )
        assert updated.skill_repo_url == "https://example.com/new-skills.git"
        assert seen["skill_repo_url"] == "https://example.com/new-skills.git"
    finally:
        db_session.close()


def test_t13_defaults_expose_dual_runtime_choice_matrix(monkeypatch):
    monkeypatch.setattr(agents_api.settings, "default_agent_image_repo", "ghcr.io/example/efp-runtime")
    monkeypatch.setattr(agents_api.settings, "default_agent_image_tag", "test-runtime")

    defaults = agents_api.get_agent_defaults(_fake_user())
    assert defaults["default_runtime_type"] == "native"
    assert [item["value"] for item in defaults["runtime_types"]] == ["native", "opencode"]
    assert defaults["image_repo"] == "ghcr.io/example/efp-runtime"
    assert defaults["image_tag"] == "test-runtime"
    assert defaults["mount_path"] == "/workspace"


def test_t13_proxy_route_keeps_agent_api_prefix():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/a/{agent_id}/{subpath:path}" in paths
    assert "/a/{agent_id}/api/events" in paths
    assert all(not p.startswith("/opencode/") for p in paths)
