import asyncio
from types import SimpleNamespace

from app.api import agents as agents_api
from app.main import app
from app.db import Base
from app.models import Agent, User
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _fake_user():
    return SimpleNamespace(id=1, role="admin")


def _create_payload(runtime_type: str):
    return agents_api.AgentCreateRequest(name=f"agent-{runtime_type}", runtime_type=runtime_type)


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


def test_t13_create_native_and_opencode_agents_select_correct_defaults(monkeypatch):
    db_session = _build_db()
    monkeypatch.setattr(agents_api, "get_current_user", lambda: _fake_user())
    monkeypatch.setattr(agents_api.settings, "default_native_runtime_image_repo", "ghcr.io/example/efp-native-runtime")
    monkeypatch.setattr(agents_api.settings, "default_native_runtime_image_tag", "test-native")
    monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_repo", "ghcr.io/example/efp-opencode-runtime")
    monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_tag", "1.14.29")
    monkeypatch.setattr(agents_api.settings, "default_skill_repo_url", "https://example.com/skills.git")
    monkeypatch.setattr(agents_api.settings, "default_tool_repo_url", "https://example.com/tools.git")

    calls = []
    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda agent: calls.append(agent.id) or SimpleNamespace(status="running", message=None))

    try:
        native = agents_api.create_agent(_create_payload("native"), _fake_user(), db_session)
        opencode = agents_api.create_agent(_create_payload("opencode"), _fake_user(), db_session)

        assert native.runtime_type == "native"
        assert "ghcr.io/example/efp-native-runtime:test-native" in native.image
        native_db = db_session.query(Agent).filter(Agent.id == native.id).one()
        opencode_db = db_session.query(Agent).filter(Agent.id == opencode.id).one()
        assert native_db.mount_path == "/root/.efp"
        assert native_db.service_name.startswith("agent-")
        assert opencode.runtime_type == "opencode"
        assert "ghcr.io/example/efp-opencode-runtime:1.14.29" in opencode.image
        assert opencode_db.mount_path == "/workspace"
        assert opencode_db.service_name.startswith("agent-")
        assert len(calls) == 2
    finally:
        db_session.close()


def test_t13_edit_runtime_type_both_directions_switches_image_mount_and_reprovisions(monkeypatch):
    db_session = _build_db()
    monkeypatch.setattr(agents_api.settings, "default_native_runtime_image_repo", "ghcr.io/example/efp-native-runtime")
    monkeypatch.setattr(agents_api.settings, "default_native_runtime_image_tag", "test-native")
    monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_repo", "ghcr.io/example/efp-opencode-runtime")
    monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_tag", "1.14.29")
    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    updates = []
    monkeypatch.setattr(agents_api.k8s_service, "update_agent_runtime", lambda agent: updates.append((agent.runtime_type, agent.mount_path, agent.image)) or SimpleNamespace(status="running", message=None))

    try:
        created = agents_api.create_agent(_create_payload("native"), _fake_user(), db_session)
        updated_to_opencode = asyncio.run(agents_api.update_agent(created.id, agents_api.AgentUpdateRequest(runtime_type="opencode"), _fake_user(), db_session))
        first_update = updates[-1]
        updated_to_native = asyncio.run(agents_api.update_agent(created.id, agents_api.AgentUpdateRequest(runtime_type="native"), _fake_user(), db_session))

        assert updated_to_opencode.runtime_type == "opencode"
        assert first_update[0] == "opencode"
        assert first_update[1] == "/workspace"
        assert "ghcr.io/example/efp-opencode-runtime:1.14.29" in updated_to_opencode.image
        assert updated_to_native.runtime_type == "native"
        native_db = db_session.query(Agent).filter(Agent.id == created.id).one()
        assert native_db.mount_path == "/root/.efp"
        assert "ghcr.io/example/efp-native-runtime:test-native" in updated_to_native.image
        assert len(updates) >= 2
    finally:
        db_session.close()


def test_t13_skill_and_tool_repo_changes_trigger_runtime_rollout(monkeypatch):
    db_session = _build_db()
    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    seen = {}
    monkeypatch.setattr(agents_api.k8s_service, "update_agent_runtime", lambda agent: seen.update(skill_repo_url=agent.skill_repo_url, tool_repo_url=agent.tool_repo_url) or SimpleNamespace(status="running", message=None))

    try:
        created = agents_api.create_agent(_create_payload("opencode"), _fake_user(), db_session)
        updated = asyncio.run(
            agents_api.update_agent(
                created.id,
                agents_api.AgentUpdateRequest(skill_repo_url="https://example.com/new-skills.git", tool_repo_url="https://example.com/new-tools.git"),
                _fake_user(),
                db_session,
            )
        )
        assert updated.skill_repo_url == "https://example.com/new-skills.git"
        assert updated.tool_repo_url == "https://example.com/new-tools.git"
        assert seen["skill_repo_url"] == "https://example.com/new-skills.git"
        assert seen["tool_repo_url"] == "https://example.com/new-tools.git"
    finally:
        db_session.close()


def test_t13_defaults_exposes_native_and_opencode_runtime_choices(monkeypatch):
    monkeypatch.setattr(agents_api.settings, "default_runtime_type", "native")
    monkeypatch.setattr(agents_api.settings, "default_native_runtime_image_repo", "ghcr.io/example/efp-native-runtime")
    monkeypatch.setattr(agents_api.settings, "default_native_runtime_image_tag", "test-native")
    monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_repo", "ghcr.io/example/efp-opencode-runtime")
    monkeypatch.setattr(agents_api.settings, "default_opencode_runtime_image_tag", "1.14.29")
    monkeypatch.setattr(agents_api.settings, "default_tool_repo_url", "https://example.com/tools.git")
    monkeypatch.setattr(agents_api.settings, "default_tool_branch", "main")

    defaults = agents_api.get_agent_defaults(_fake_user())
    assert defaults["default_runtime_type"] == "native"
    values = {item["value"] for item in defaults["runtime_types"]}
    assert values == {"native", "opencode"}
    native_cfg = next(item for item in defaults["runtime_types"] if item["value"] == "native")
    opencode_cfg = next(item for item in defaults["runtime_types"] if item["value"] == "opencode")
    assert native_cfg["image_repo"] == "ghcr.io/example/efp-native-runtime"
    assert native_cfg["image_tag"] == "test-native"
    assert native_cfg["default_mount_path"] == "/root/.efp"
    assert opencode_cfg["image_repo"] == "ghcr.io/example/efp-opencode-runtime"
    assert opencode_cfg["image_tag"] == "1.14.29"
    assert opencode_cfg["default_mount_path"] == "/workspace"
    assert defaults["default_tool_repo_url"] == "https://example.com/tools.git"
    assert defaults["default_tool_branch"] == "main"


def test_t13_runtime_profile_sync_uses_internal_apply_contract_for_all_runtime_types(monkeypatch):
    for runtime_type in ("native", "opencode"):
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        captured = {}

        async def _fake_forward(**kwargs):
            captured.update(kwargs)
            return 200, b"{}", "application/json"

        monkeypatch.setattr(service.proxy_service, "forward", _fake_forward)
        agent = SimpleNamespace(
            id=f"a-{runtime_type}",
            namespace="efp-agents",
            service_name=f"svc-{runtime_type}",
            runtime_type=runtime_type,
        )
        result = asyncio.run(service.push_payload_to_agent(agent, {"x": 1}))
        assert result.ok is True
        assert result.apply_status == "applied"
        assert captured["subpath"] == "api/internal/runtime-profile/apply"
        assert "/config" not in captured["subpath"]
        assert "/auth" not in captured["subpath"]


def test_t13_proxy_route_keeps_agent_api_prefix():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/a/{agent_id}/{subpath:path}" in paths
    assert "/a/{agent_id}/api/events" in paths
    assert all(not p.startswith("/opencode/") for p in paths)
