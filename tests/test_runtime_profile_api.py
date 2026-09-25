import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User
from app.models.runtime_profile import RuntimeProfile


class _FakeSecretService:
    def __init__(self):
        self.saved_profile_ids: list[str] = []

    def apply_profile_save(self, _db, profile):
        self.saved_profile_ids.append(profile.id)
        return {"restarted_agent_ids": [], "pending_agent_ids": [], "failed_agent_ids": []}


def _build_client(monkeypatch):
    from app.main import app
    import app.deps as deps_module
    import app.api.runtime_profiles as runtime_profiles_api

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

    fake_secrets = _FakeSecretService()
    monkeypatch.setattr(runtime_profiles_api, "runtime_profile_secret_service", fake_secrets)
    app.dependency_overrides[deps_module.get_current_user] = _override_user
    app.dependency_overrides[runtime_profiles_api.get_db] = _override_db

    def _set_user(user):
        state["user"] = user

    def _cleanup():
        app.dependency_overrides.clear()
        db.close()

    return SimpleNamespace(
        client=TestClient(app),
        db=db,
        u1=user1,
        u2=user2,
        set_user=_set_user,
        cleanup=_cleanup,
        secrets=fake_secrets,
    )


def test_get_returns_the_callers_own_row_and_creates_it_once(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        first = env.client.get("/api/runtime-profile")
        assert first.status_code == 200
        body = first.json()
        assert body["owner_user_id"] == env.u1.id
        assert set(body) == {"id", "owner_user_id", "config_json", "revision", "created_at", "updated_at"}

        again = env.client.get("/api/runtime-profile").json()
        assert again["id"] == body["id"]
        assert env.db.query(RuntimeProfile).filter_by(owner_user_id=env.u1.id).count() == 1

        env.set_user(env.u2)
        theirs = env.client.get("/api/runtime-profile").json()
        assert theirs["owner_user_id"] == env.u2.id
        assert theirs["id"] != body["id"]
    finally:
        env.cleanup()


def test_patch_saves_the_config_and_bumps_revision_only_on_change(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        start = env.client.get("/api/runtime-profile").json()

        config = json.dumps({"github": {"enabled": True, "api_token": "ghp_x"}})
        saved = env.client.patch("/api/runtime-profile", json={"config_json": config})
        assert saved.status_code == 200
        assert saved.json()["id"] == start["id"]
        assert saved.json()["revision"] == start["revision"] + 1
        stored = json.loads(env.db.get(RuntimeProfile, start["id"]).config_json)
        assert stored["github"]["api_token"] == "ghp_x"
        assert env.secrets.saved_profile_ids == [start["id"]]

        # The same settings again are not a change: no revision bump, no rollout.
        same = env.client.patch("/api/runtime-profile", json={"config_json": config})
        assert same.status_code == 200
        assert same.json()["revision"] == start["revision"] + 1
        assert env.secrets.saved_profile_ids == [start["id"]]

        # No config at all leaves everything as it was.
        empty = env.client.patch("/api/runtime-profile", json={})
        assert empty.status_code == 200
        assert empty.json()["revision"] == start["revision"] + 1
    finally:
        env.cleanup()


def test_patch_only_touches_the_callers_row(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        theirs = env.client.get("/api/runtime-profile").json()
        env.set_user(env.u2)
        env.client.patch("/api/runtime-profile", json={"config_json": json.dumps({"github": {"enabled": True}})})

        assert json.loads(env.db.get(RuntimeProfile, theirs["id"]).config_json) == {}
    finally:
        env.cleanup()


def test_patch_rejects_invalid_config_json(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        for bad in ("not-json", "[1, 2]"):
            resp = env.client.patch("/api/runtime-profile", json={"config_json": bad})
            assert resp.status_code == 422, bad
        assert env.secrets.saved_profile_ids == []
    finally:
        env.cleanup()


def test_first_row_without_a_seed_is_empty(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        resp = env.client.get("/api/runtime-profile")
        assert resp.status_code == 200
        assert json.loads(resp.json()["config_json"]) == {}
    finally:
        env.cleanup()


def test_runtime_profile_get_sanitizes_legacy_provider_automation_fields(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        legacy = RuntimeProfile(
            owner_user_id=env.u1.id,
            name="Legacy Profile",
            config_json=json.dumps(
                {
                    "github": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
                    "jira": {"enabled": True, "automation": {"assignments": {"enabled": True}}},
                    "confluence": {"enabled": True, "automation": {"mentions": {"enabled": True}}},
                }
            ),
            revision=1,
            is_default=True,
        )
        env.db.add(legacy)
        env.db.commit()
        env.db.refresh(legacy)

        resp = env.client.get("/api/runtime-profile")
        assert resp.status_code == 200
        assert resp.json()["id"] == legacy.id
        cfg = json.loads(resp.json()["config_json"])
        assert cfg["github"] == {"enabled": True, "api_token_present": False}
        assert cfg["jira"] == {"enabled": True}
        assert cfg["confluence"] == {"enabled": True}
    finally:
        env.cleanup()


def test_runtime_profile_api_redacts_llm_oauth_secrets_in_response(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        profile = RuntimeProfile(owner_user_id=env.u1.id, name="OAuth Redact", config_json=json.dumps({"llm":{"provider":"github_copilot","oauth":{"type":"oauth","access":"gho_A","refresh":"gho_R","expires":0}}}), revision=1, is_default=True)
        env.db.add(profile); env.db.commit(); env.db.refresh(profile)
        resp = env.client.get("/api/runtime-profile")
        assert resp.status_code == 200
        assert "gho_A" not in resp.text and "gho_R" not in resp.text
        cfg = json.loads(resp.json()["config_json"])
        assert cfg["llm"]["api_key_present"] is False
        assert "api_key" not in cfg["llm"]
        assert "oauth" not in cfg["llm"]
    finally:
        env.cleanup()


def test_runtime_profile_api_redaction_does_not_remove_persisted_oauth(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        payload = {"llm":{"provider":"github_copilot","oauth":{"type":"oauth","access":"gho_A","refresh":"gho_R","expires":0}}}
        profile = RuntimeProfile(owner_user_id=env.u1.id, name="OAuth Persist", config_json=json.dumps(payload), revision=1, is_default=True)
        env.db.add(profile); env.db.commit(); env.db.refresh(profile)
        _ = env.client.get("/api/runtime-profile")
        env.db.refresh(profile)
        saved = json.loads(profile.config_json)
        assert saved["llm"]["oauth"]["access"] == "gho_A"
        assert saved["llm"]["oauth"]["refresh"] == "gho_R"
    finally:
        env.cleanup()


def test_patch_response_redacts_saved_secrets(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        resp = env.client.patch(
            "/api/runtime-profile",
            json={"config_json": json.dumps({"github": {"enabled": True, "api_token": "ghp_secret"}})},
        )
        assert resp.status_code == 200
        assert "ghp_secret" not in resp.text
        assert json.loads(resp.json()["config_json"])["github"]["api_token_present"] is True
    finally:
        env.cleanup()


# ------------------------------------------------ where a member's row starts

# A member's settings row starts from the admin-maintained Default Connections
# when there are any, so the member lands on connectors already pointing at the
# right services.


def _seed(db, config):
    from app.services.runtime_profile_seed_service import RuntimeProfileSeedService

    RuntimeProfileSeedService(db).save_seed(config)


SHARED_SEED = {
    "jira": {
        "enabled": True,
        "instances": [{"name": "Prod", "url": "https://company.atlassian.net", "token": "shared-token"}],
    }
}


def test_first_row_carries_the_shared_default_connections_credentials(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        _seed(env.db, SHARED_SEED)

        created = env.client.get("/api/runtime-profile")
        assert created.status_code == 200

        # The response redacts the token, so assert against what was persisted.
        stored = env.db.get(RuntimeProfile, created.json()["id"])
        assert json.loads(stored.config_json)["jira"]["instances"][0]["token"] == "shared-token"

        # And the response says a token is set without disclosing it.
        instance = json.loads(created.json()["config_json"])["jira"]["instances"][0]
        assert instance["token_present"] is True
        assert "token" not in instance
    finally:
        env.cleanup()


def test_an_existing_row_is_not_reseeded(monkeypatch):
    env = _build_client(monkeypatch)
    try:
        first = env.client.get("/api/runtime-profile").json()
        _seed(env.db, SHARED_SEED)

        later = env.client.get("/api/runtime-profile").json()
        assert later["id"] == first["id"]
        assert json.loads(env.db.get(RuntimeProfile, first["id"]).config_json) == {}
    finally:
        env.cleanup()
