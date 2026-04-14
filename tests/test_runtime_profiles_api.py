import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User


def _build_client(monkeypatch):
    from app.main import app
    import app.deps as deps_module
    import app.api.runtime_profiles as runtime_profiles_api
    import app.api.agents as agents_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    admin = User(username="admin", password_hash="test", role="admin", is_active=True)
    viewer = User(username="viewer", password_hash="test", role="viewer", is_active=True)
    db.add_all([admin, viewer])
    db.commit()
    db.refresh(admin)
    db.refresh(viewer)

    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))

    state = {"user": admin}

    def _override_user():
        user = state["user"]
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)

    def _override_db():
        yield db

    app.dependency_overrides[deps_module.get_current_user] = _override_user
    app.dependency_overrides[runtime_profiles_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_current_user] = _override_user

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    def _set_user(user):
        state["user"] = user

    return TestClient(app), _set_user, admin, viewer, _cleanup


def test_runtime_profiles_crud_and_validation(monkeypatch):
    client, _set_user, _admin, _viewer, cleanup = _build_client(monkeypatch)
    try:
        bad_json = client.post("/api/runtime-profiles", json={"name": "rp1", "config_json": "{"})
        assert bad_json.status_code == 422

        bad_section = client.post("/api/runtime-profiles", json={"name": "rp2", "config_json": json.dumps({"ssh": {}})})
        assert bad_section.status_code == 422

        create = client.post(
            "/api/runtime-profiles",
            json={"name": "Default Runtime", "description": "base", "config_json": json.dumps({"llm": {"provider": "openai"}})},
        )
        assert create.status_code == 200
        profile = create.json()
        assert profile["revision"] == 1

        list_resp = client.get("/api/runtime-profiles")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        get_resp = client.get(f"/api/runtime-profiles/{profile['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Default Runtime"

        patch_resp = client.patch(
            f"/api/runtime-profiles/{profile['id']}",
            json={"config_json": json.dumps({"llm": {"provider": "anthropic"}, "debug": {"enabled": True}})},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["revision"] == 2

        delete_resp = client.delete(f"/api/runtime-profiles/{profile['id']}")
        assert delete_resp.status_code == 200
    finally:
        cleanup()


def test_runtime_profile_delete_conflict_when_referenced(monkeypatch):
    client, _set_user, _admin, _viewer, cleanup = _build_client(monkeypatch)
    try:
        rp = client.post("/api/runtime-profiles", json={"name": "InUse", "config_json": "{}"}).json()
        create_agent = client.post(
            "/api/agents",
            json={"name": "A1", "image": "example/image:latest", "runtime_profile_id": rp["id"]},
        )
        assert create_agent.status_code == 200

        delete_resp = client.delete(f"/api/runtime-profiles/{rp['id']}")
        assert delete_resp.status_code == 409
    finally:
        cleanup()


def test_runtime_profile_options_endpoint_for_non_admin(monkeypatch):
    client, set_user, _admin, viewer, cleanup = _build_client(monkeypatch)
    try:
        create = client.post(
            "/api/runtime-profiles",
            json={"name": "Option Runtime", "description": "d", "config_json": json.dumps({"llm": {"provider": "openai"}})},
        )
        assert create.status_code == 200

        set_user(viewer)
        resp = client.get("/api/runtime-profiles/options")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert set(body[0].keys()) == {"id", "name", "description", "revision"}
        assert "config_json" not in body[0]
    finally:
        cleanup()


def test_runtime_profile_patch_triggers_fanout_sync(monkeypatch):
    client, _set_user, _admin, _viewer, cleanup = _build_client(monkeypatch)
    try:
        create = client.post(
            "/api/runtime-profiles",
            json={"name": "Sync Runtime", "config_json": json.dumps({"llm": {"provider": "openai"}})},
        )
        assert create.status_code == 200
        profile = create.json()

        calls = []

        async def _fake_sync(db, runtime_profile):
            calls.append(runtime_profile.id)
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.api.runtime_profiles.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        patch_resp = client.patch(
            f"/api/runtime-profiles/{profile['id']}",
            json={"config_json": json.dumps({"llm": {"provider": "anthropic"}})},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["id"] == profile["id"]
        assert calls == [profile["id"]]
    finally:
        cleanup()


def test_runtime_profile_patch_sync_exception_does_not_break_response(monkeypatch):
    client, _set_user, _admin, _viewer, cleanup = _build_client(monkeypatch)
    try:
        create = client.post(
            "/api/runtime-profiles",
            json={"name": "Sync Exception Runtime", "config_json": json.dumps({"llm": {"provider": "openai"}})},
        )
        profile = create.json()

        async def _boom(_db, _runtime_profile):
            raise RuntimeError("sync failed")

        monkeypatch.setattr("app.api.runtime_profiles.runtime_profile_sync_service.sync_profile_to_bound_agents", _boom)
        patch_resp = client.patch(
            f"/api/runtime-profiles/{profile['id']}",
            json={"config_json": json.dumps({"llm": {"provider": "anthropic"}})},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["id"] == profile["id"]
    finally:
        cleanup()
