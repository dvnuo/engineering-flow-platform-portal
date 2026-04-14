import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile


def _build_client(monkeypatch):
    from app.main import app
    import app.web as web_module

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)

    db = TestingSessionLocal()
    owner = User(username="owner", password_hash="test", role="user", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)

    profile = RuntimeProfile(
        owner_user_id=owner.id,
        name="Owner Profile",
        description="desc",
        config_json=json.dumps({"llm": {"provider": "openai"}}),
        revision=1,
        is_default=True,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    agent = Agent(
        name="agent-1",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep",
        service_name="svc",
        pvc_name="pvc",
        endpoint_path="/",
        agent_type="workspace",
        runtime_profile_id=profile.id,
    )
    db.add(agent)
    db.commit()

    monkeypatch.setattr(web_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(
        web_module,
        "_current_user_from_cookie",
        lambda _request: SimpleNamespace(id=owner.id, role="user", username=owner.username, nickname=owner.username),
    )

    async def _fake_sync(_db, _profile):
        return {"updated_running_count": 1, "skipped_not_running_count": 0, "failed_agent_ids": []}

    monkeypatch.setattr(web_module.runtime_profile_sync_service, "sync_profile_to_bound_agents", _fake_sync)

    def _cleanup():
        db.close()

    return TestClient(app), profile, _cleanup


def test_runtime_profile_management_template_and_js_hooks_exist():
    app_html = open("app/templates/app.html", "r", encoding="utf-8").read()
    js = open("app/static/js/chat_ui.js", "r", encoding="utf-8").read()
    assert 'id="runtime-profiles-menu-btn"' in app_html
    assert 'id="runtime-profiles-nav-section"' in app_html
    assert 'id="add-runtime-profile-btn"' in app_html
    assert '"runtime-profiles"' in js


def test_runtime_profile_panel_save_and_set_default(monkeypatch):
    client, profile, cleanup = _build_client(monkeypatch)
    try:
        panel = client.get(f"/app/runtime-profiles/{profile.id}/panel")
        assert panel.status_code == 200
        assert "Owner Profile" in panel.text

        save = client.post(
            f"/app/runtime-profiles/{profile.id}/save",
            data={"name": "Owner Profile", "description": "new", "llm_provider": "anthropic", "llm_model": "claude"},
        )
        assert save.status_code == 200
        assert "Owner Profile" in save.text
        assert "Owner Profile" in save.text

        set_default = client.post(f"/app/runtime-profiles/{profile.id}/set-default")
        assert set_default.status_code == 200
        assert "Default runtime profile updated." in set_default.text
    finally:
        cleanup()
