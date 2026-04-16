import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User


def _build_client(monkeypatch):
    from app.main import app
    import app.deps as deps_module
    import app.api.runtime_profiles as runtime_profiles_api
    import app.api.agents as agents_api

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    user1 = User(username="u1", password_hash="test", role="user", is_active=True)
    user2 = User(username="u2", password_hash="test", role="user", is_active=True)
    db.add_all([user1, user2])
    db.commit()
    db.refresh(user1)
    db.refresh(user2)

    state = {"user": user1}

    def _override_user():
        u = state["user"]
        return SimpleNamespace(id=u.id, role=u.role, username=u.username, nickname=u.username)

    def _override_db():
        yield db

    monkeypatch.setattr(agents_api.k8s_service, "create_agent_runtime", lambda _agent: SimpleNamespace(status="running", message=None))
    app.dependency_overrides[deps_module.get_current_user] = _override_user
    app.dependency_overrides[runtime_profiles_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_db] = _override_db
    app.dependency_overrides[agents_api.get_current_user] = _override_user

    def _set_user(user):
        state["user"] = user

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return TestClient(app), db, user1, user2, _set_user, _cleanup


def test_runtime_profiles_scoped_and_defaults(monkeypatch):
    client, db, u1, u2, set_user, cleanup = _build_client(monkeypatch)
    try:
        r1 = client.post("/api/runtime-profiles", json={"name": "Default", "description": "a", "config_json": json.dumps({"llm": {"provider": "openai"}})})
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["owner_user_id"] == u1.id
        assert body1["is_default"] is True

        r2 = client.post("/api/runtime-profiles", json={"name": "Reviewer", "config_json": "{}"})
        assert r2.status_code == 200
        body2 = r2.json()

        options_ordered = client.get("/api/runtime-profiles/options")
        assert options_ordered.status_code == 200
        ordered_names = [item["name"] for item in options_ordered.json()]
        assert ordered_names[:2] == ["Reviewer", "Default"]

        profiles_ordered = client.get("/api/runtime-profiles")
        assert profiles_ordered.status_code == 200
        ordered_profile_names = [item["name"] for item in profiles_ordered.json()]
        assert ordered_profile_names[:2] == ["Reviewer", "Default"]

        dup = client.post("/api/runtime-profiles", json={"name": "Reviewer", "config_json": "{}"})
        assert dup.status_code == 409

        set_user(u2)
        same_name_other_user = client.post("/api/runtime-profiles", json={"name": "Reviewer", "config_json": "{}"})
        assert same_name_other_user.status_code == 200

        options = client.get("/api/runtime-profiles/options")
        assert options.status_code == 200
        assert len(options.json()) == 1
        assert options.json()[0]["is_default"] is True

        list_resp = client.get("/api/runtime-profiles")
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["owner_user_id"] == u2.id

        # cross-user read -> 404
        not_found = client.get(f"/api/runtime-profiles/{body1['id']}")
        assert not_found.status_code == 404

        set_user(u1)
        switch_default = client.patch(f"/api/runtime-profiles/{body2['id']}", json={"is_default": True})
        assert switch_default.status_code == 200
        options = client.get("/api/runtime-profiles/options").json()
        assert len([p for p in options if p["is_default"]]) == 1
        assert any(p["id"] == body2["id"] and p["is_default"] for p in options)

        # cannot delete last profile
        del1 = client.delete(f"/api/runtime-profiles/{body2['id']}")
        assert del1.status_code == 200
        del_last = client.delete(f"/api/runtime-profiles/{body1['id']}")
        assert del_last.status_code == 409

        # in-use profile cannot delete
        p = client.post("/api/runtime-profiles", json={"name": "InUse", "is_default": True, "config_json": "{}"}).json()
        agent = Agent(
            name="a1",
            owner_user_id=u1.id,
            visibility="private",
            status="running",
            image="example/image:latest",
            runtime_profile_id=p["id"],
            disk_size_gi=20,
            mount_path="/root/.efp",
            namespace="efp-agents",
            deployment_name="dep",
            service_name="svc",
            pvc_name="pvc",
            endpoint_path="/",
            agent_type="workspace",
        )
        db.add(agent)
        db.commit()

        del_used = client.delete(f"/api/runtime-profiles/{p['id']}")
        assert del_used.status_code == 409
    finally:
        cleanup()


def test_runtime_profile_create_materializes_creation_seed_defaults(monkeypatch):
    client, _db, _u1, _u2, _set_user, cleanup = _build_client(monkeypatch)
    try:
        no_config = client.post(
            "/api/runtime-profiles",
            json={"name": "Seeded-Implicit", "description": "d", "is_default": False},
        )
        assert no_config.status_code == 200
        no_config_payload = json.loads(no_config.json()["config_json"])
        assert no_config_payload["proxy"]["url"] == "https://proxy.com:80"
        assert len(no_config_payload["jira"]["instances"]) == 2
        assert len(no_config_payload["confluence"]["instances"]) == 2

        empty_config = client.post(
            "/api/runtime-profiles",
            json={"name": "Seeded-Empty", "description": "d", "is_default": False, "config_json": "{}"},
        )
        assert empty_config.status_code == 200
        empty_config_payload = json.loads(empty_config.json()["config_json"])
        assert empty_config_payload["proxy"]["url"] == "https://proxy.com:80"
        assert len(empty_config_payload["jira"]["instances"]) == 2
        assert len(empty_config_payload["confluence"]["instances"]) == 2
    finally:
        cleanup()
