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

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    other = User(username="other", password_hash="test", role="user", is_active=True)
    db.add_all([owner, other])
    db.commit()
    db.refresh(owner)
    db.refresh(other)

    state = {"user": owner}

    def _override_user():
        user = state["user"]
        return SimpleNamespace(id=user.id, role=user.role, username=user.username, nickname=user.username)

    def _override_db():
        yield db

    app.dependency_overrides[deps_module.get_current_user] = _override_user
    app.dependency_overrides[runtime_profiles_api.get_db] = _override_db

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    def _set_user(user):
        state["user"] = user

    return TestClient(app), _set_user, owner, other, _cleanup


def test_runtime_profiles_user_scoped_crud_and_default(monkeypatch):
    client, set_user, owner, other, cleanup = _build_client(monkeypatch)
    try:
        first = client.post(
            "/api/runtime-profiles",
            json={"name": "Default Runtime", "description": "base", "config_json": json.dumps({"llm": {"provider": "openai"}})},
        )
        assert first.status_code == 200
        first_profile = first.json()
        assert first_profile["owner_user_id"] == owner.id
        assert first_profile["is_default"] is True

        second = client.post("/api/runtime-profiles", json={"name": "Extra", "config_json": "{}"})
        assert second.status_code == 200
        second_profile = second.json()
        assert second_profile["is_default"] is False

        options = client.get("/api/runtime-profiles/options")
        assert options.status_code == 200
        payload = options.json()
        assert len(payload) == 2
        assert {"id", "name", "description", "revision", "is_default"}.issubset(payload[0].keys())

        set_default = client.post(f"/api/runtime-profiles/{second_profile['id']}/set-default")
        assert set_default.status_code == 200
        assert set_default.json()["is_default"] is True

        list_resp = client.get("/api/runtime-profiles")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 2
        winner = [item for item in list_resp.json() if item["is_default"]]
        assert len(winner) == 1
        assert winner[0]["id"] == second_profile["id"]

        set_user(other)
        hidden = client.get(f"/api/runtime-profiles/{second_profile['id']}")
        assert hidden.status_code == 404
        hidden_options = client.get("/api/runtime-profiles/options")
        assert hidden_options.status_code == 200
        assert hidden_options.json() == []
    finally:
        cleanup()


def test_runtime_profile_delete_rules(monkeypatch):
    client, _set_user, _owner, _other, cleanup = _build_client(monkeypatch)
    try:
        first = client.post("/api/runtime-profiles", json={"name": "one", "config_json": "{}"}).json()
        only_delete = client.delete(f"/api/runtime-profiles/{first['id']}")
        assert only_delete.status_code == 409
        assert only_delete.json()["detail"] == "Each user must keep at least one runtime profile."

        second = client.post("/api/runtime-profiles", json={"name": "two", "config_json": "{}"}).json()
        delete_default = client.delete(f"/api/runtime-profiles/{first['id']}")
        assert delete_default.status_code == 200

        listed = client.get("/api/runtime-profiles").json()
        assert len(listed) == 1
        assert listed[0]["id"] == second["id"]
        assert listed[0]["is_default"] is True
    finally:
        cleanup()


def test_runtime_profile_patch_sync_and_validation(monkeypatch):
    client, _set_user, _owner, _other, cleanup = _build_client(monkeypatch)
    try:
        created = client.post(
            "/api/runtime-profiles",
            json={"name": "sync", "config_json": json.dumps({"llm": {"provider": "openai"}})},
        ).json()

        calls = []

        async def _fake_sync(db, runtime_profile):
            calls.append(runtime_profile.id)
            return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

        monkeypatch.setattr("app.api.runtime_profiles.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

        patch_resp = client.patch(
            f"/api/runtime-profiles/{created['id']}",
            json={"config_json": json.dumps({"llm": {"provider": "anthropic"}})},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["revision"] == 2
        assert calls == [created["id"]]

        dup = client.post("/api/runtime-profiles", json={"name": "sync", "config_json": "{}"})
        assert dup.status_code == 409
    finally:
        cleanup()
