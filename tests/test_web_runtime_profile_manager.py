import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import RuntimeProfile, User


def _build_client(monkeypatch):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    other = User(username="other", password_hash="test", role="user", is_active=True)
    db.add_all([owner, other]); db.commit(); db.refresh(owner); db.refresh(other)

    rp = RuntimeProfile(owner_user_id=owner.id, name="Default", description="d", config_json=json.dumps({"llm": {"provider": "openai"}}), revision=1, is_default=True)
    db.add(rp); db.commit(); db.refresh(rp)

    state = {"user": owner}
    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=state["user"].id, role="user", username=state["user"].username, nickname=state["user"].username),
    )

    async def _fake_sync(*_args, **_kwargs):
        return {"updated_running_count": 0, "skipped_not_running_count": 0, "failed_agent_ids": []}

    monkeypatch.setattr("app.web.runtime_profile_sync_service.sync_profile_to_bound_agents", _fake_sync)

    def _set_user(u):
        state["user"] = u

    def _cleanup():
        db.close()

    return TestClient(app), db, owner, other, rp, _set_user, _cleanup


def test_runtime_profile_panel_owner_only(monkeypatch):
    client, _db, owner, other, rp, set_user, cleanup = _build_client(monkeypatch)
    try:
        ok = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert ok.status_code == 200
        assert "Runtime Profile Metadata" in ok.text

        set_user(other)
        deny = client.get(f"/app/runtime-profiles/{rp.id}/panel")
        assert deny.status_code == 404
    finally:
        cleanup()


def test_runtime_profile_save_updates_and_triggers(monkeypatch):
    client, db, owner, _other, rp, _set_user, cleanup = _build_client(monkeypatch)
    try:
        resp = client.post(
            f"/app/runtime-profiles/{rp.id}/save",
            data={
                "name": "Renamed",
                "description": "new-desc",
                "is_default": "on",
                "llm_provider": "anthropic",
                "llm_model": "claude-sonnet-4",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "runtimeProfilesChanged"

        db.refresh(rp)
        assert rp.name == "Renamed"
        assert rp.description == "new-desc"
        assert rp.revision == 2
        saved = json.loads(rp.config_json)
        assert saved["llm"]["provider"] == "anthropic"
    finally:
        cleanup()
