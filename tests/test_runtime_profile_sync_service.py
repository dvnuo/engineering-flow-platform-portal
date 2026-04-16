import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.runtime_profile_sync_service import RuntimeProfileSyncService

EXPECTED_PROXY_URL = "https://proxy.com:80"
EXPECTED_DEFAULT_LLM_PROVIDER = "github_copilot"
EXPECTED_DEFAULT_LLM_MODEL = "gpt-5-mini"
EXPECTED_JIRA_INSTANCES = [
    {"name": "Jira 1", "url": "https://yourcompany.atlassian.net"},
    {"name": "Jira 2", "url": "https://yourcompany2.atlassian.net"},
]
EXPECTED_CONFLUENCE_INSTANCES = [
    {"name": "Confluence 1", "url": "https://yourcompany.atlassian.net/wiki"},
    {"name": "Confluence 2", "url": "https://yourcompany2.atlassian.net/wiki"},
]


def _build_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    owner = User(username="owner", password_hash="test", role="admin", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)

    rp = RuntimeProfile(owner_user_id=owner.id, name="rp-sync-service", config_json='{"llm": {"provider": "openai"}, "ssh": {"x":1}}', revision=3, is_default=True)
    db.add(rp)
    db.commit()
    db.refresh(rp)

    running = Agent(
        name="running-agent",
        owner_user_id=owner.id,
        visibility="private",
        status="running",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-running",
        service_name="svc-running",
        pvc_name="pvc-running",
        endpoint_path="/",
        agent_type="workspace",
        runtime_profile_id=rp.id,
    )
    stopped = Agent(
        name="stopped-agent",
        owner_user_id=owner.id,
        visibility="private",
        status="stopped",
        image="example/image:latest",
        repo_url=None,
        branch=None,
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name="dep-stopped",
        service_name="svc-stopped",
        pvc_name="pvc-stopped",
        endpoint_path="/",
        agent_type="workspace",
        runtime_profile_id=rp.id,
    )
    db.add_all([running, stopped])
    db.commit()
    db.refresh(running)
    db.refresh(stopped)

    return db, rp, running, stopped


def test_push_payload_to_agent_swallows_forward_exception(monkeypatch):
    db, rp, running, _stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))

        async def _raise_forward(**_kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(service.proxy_service, "forward", _raise_forward)

        ok = asyncio.run(service.push_payload_to_agent(running, service.build_apply_payload_from_profile(rp)))
        assert ok is False
    finally:
        db.close()


def test_sync_profile_to_bound_agents_collects_failures_without_raising(monkeypatch):
    db, rp, running, stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))

        async def _raise_forward(**_kwargs):
            raise RuntimeError("runtime unreachable")

        monkeypatch.setattr(service.proxy_service, "forward", _raise_forward)

        result = asyncio.run(service.sync_profile_to_bound_agents(db, rp))
        assert result["updated_running_count"] == 0
        assert result["skipped_not_running_count"] == 1
        assert running.id in result["failed_agent_ids"]
        assert stopped.id not in result["failed_agent_ids"]
    finally:
        db.close()


def test_push_payload_to_agent_uses_content_type_and_portal_trusted_headers(monkeypatch):
    db, rp, running, _stopped = _build_db()
    try:
        service = RuntimeProfileSyncService(proxy_service=SimpleNamespace(forward=None))
        captured = {}

        async def _fake_forward(**kwargs):
            captured.update(kwargs)
            return 200, b'{"ok": true}', "application/json"

        monkeypatch.setattr(service.proxy_service, "forward", _fake_forward)

        ok = asyncio.run(service.push_payload_to_agent(running, service.build_apply_payload_from_profile(rp)))
        assert ok is True
        assert captured["headers"] == {"content-type": "application/json"}
        assert captured["extra_headers"] == {"X-Portal-Author-Source": "portal"}
    finally:
        db.close()


def test_build_apply_payload_from_profile_keeps_shape_and_includes_materialized_seed():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = RuntimeProfileService.materialize_create_config_json("{}")
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert set(payload.keys()) == {"runtime_profile_id", "revision", "config"}
        assert payload["runtime_profile_id"] == rp.id
        assert payload["revision"] == rp.revision
        assert payload["config"]["proxy"]["url"] == EXPECTED_PROXY_URL
        assert payload["config"]["jira"]["instances"] == EXPECTED_JIRA_INSTANCES
        assert payload["config"]["confluence"]["instances"] == EXPECTED_CONFLUENCE_INSTANCES
        assert payload["config"]["llm"]["provider"] == EXPECTED_DEFAULT_LLM_PROVIDER
        assert payload["config"]["llm"]["model"] == EXPECTED_DEFAULT_LLM_MODEL
    finally:
        db.close()


def test_build_apply_payload_from_sparse_legacy_profile_does_not_backfill_creation_seed():
    db, rp, _running, _stopped = _build_db()
    try:
        rp.config_json = "{}"
        db.add(rp)
        db.commit()
        db.refresh(rp)

        payload = RuntimeProfileSyncService.build_apply_payload_from_profile(rp)
        assert set(payload.keys()) == {"runtime_profile_id", "revision", "config"}
        assert "url" not in payload["config"]["proxy"]
        assert payload["config"]["jira"]["instances"] == []
        assert payload["config"]["confluence"]["instances"] == []
        assert payload["config"]["llm"]["provider"] == EXPECTED_DEFAULT_LLM_PROVIDER
        assert payload["config"]["llm"]["model"] == EXPECTED_DEFAULT_LLM_MODEL
    finally:
        db.close()
