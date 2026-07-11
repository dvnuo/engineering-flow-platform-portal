import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, User
from app.models.runtime_profile import RuntimeProfile
from app.services.runtime_profile_secret_service import (
    NONE_SECRET_NAME,
    RuntimeProfileSecretService,
    profile_secret_name,
    render_none_secret_data,
    render_profile_secret_data,
)


def _build_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    owner = User(username="owner", password_hash="test", role="admin", is_active=True)
    db.add(owner)
    db.commit()
    db.refresh(owner)

    rp = RuntimeProfile(
        owner_user_id=owner.id,
        name="rp-secret",
        config_json='{"llm": {"provider": "github_copilot", "model": "gpt-5-mini"}}',
        revision=7,
        is_default=True,
    )
    db.add(rp)
    db.commit()
    db.refresh(rp)
    return db, owner, rp


def _make_agent(db, owner, rp, *, name: str, status: str):
    agent = Agent(
        name=name,
        owner_user_id=owner.id,
        visibility="private",
        status=status,
        image="example/image:latest",
        disk_size_gi=20,
        mount_path="/workspace",
        namespace="efp-agents",
        deployment_name=f"dep-{name}",
        service_name=f"svc-{name}",
        pvc_name=f"pvc-{name}",
        endpoint_path="/",
        agent_type="workspace",
        runtime_profile_id=rp.id,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


class _FakeK8s:
    def __init__(self, *, enabled=True, restart_status="restarting"):
        self.enabled = enabled
        self.restart_status = restart_status
        self.upserts = []
        self.deletes = []
        self.restarted = []

    def upsert_secret(self, name, string_data, namespace=None):
        self.upserts.append((name, string_data))

    def delete_secret(self, name, namespace=None):
        self.deletes.append(name)

    def restart_agent(self, agent):
        self.restarted.append(agent.id)
        return SimpleNamespace(status=self.restart_status, message=f"Restart requested: {agent.id}")


def test_render_profile_secret_data_contains_both_runtime_payloads_and_revision():
    db, _owner, rp = _build_db()
    try:
        data = render_profile_secret_data(rp)
        assert set(data.keys()) == {"native.json", "opencode.json", "revision"}
        assert data["revision"] == "7"

        native = json.loads(data["native.json"])
        opencode = json.loads(data["opencode.json"])

        for payload, runtime_type in ((native, "native"), (opencode, "opencode")):
            assert payload["runtime_profile_id"] == rp.id
            assert payload["name"] == "rp-secret"
            assert payload["revision"] == 7
            assert payload["runtime_type"] == runtime_type

        # Per-runtime projection must differ: opencode uses provider/model aliases.
        assert native["config"]["llm"]["provider"] == "github_copilot"
        assert native["config"]["llm"]["model"] == "gpt-5-mini"
        assert opencode["config"]["llm"]["provider"] == "github-copilot"
        assert opencode["config"]["llm"]["model"] == "github-copilot/gpt-5-mini"
    finally:
        db.close()


def test_render_none_secret_data_is_valid_empty_profile():
    data = render_none_secret_data()
    assert set(data.keys()) == {"native.json", "opencode.json", "revision"}
    assert data["revision"] == "0"
    for key, runtime_type in (("native.json", "native"), ("opencode.json", "opencode")):
        payload = json.loads(data[key])
        assert payload == {
            "runtime_profile_id": None,
            "name": "",
            "revision": None,
            "runtime_type": runtime_type,
            "config": {},
        }


def test_sync_profile_secret_upserts_named_secret():
    db, _owner, rp = _build_db()
    try:
        fake = _FakeK8s()
        service = RuntimeProfileSecretService(k8s_service=fake)
        service.sync_profile_secret(rp)

        assert len(fake.upserts) == 1
        name, string_data = fake.upserts[0]
        assert name == profile_secret_name(rp.id) == f"efp-profile-{rp.id}"
        assert string_data["revision"] == "7"
    finally:
        db.close()


def test_ensure_none_secret_and_delete_profile_secret():
    fake = _FakeK8s()
    service = RuntimeProfileSecretService(k8s_service=fake)
    service.ensure_none_secret()
    service.delete_profile_secret("rp-del")

    assert fake.upserts[0][0] == NONE_SECRET_NAME
    assert fake.deletes == ["efp-profile-rp-del"]


def test_secret_service_noops_when_k8s_disabled():
    db, _owner, rp = _build_db()
    try:
        fake = _FakeK8s(enabled=False)
        service = RuntimeProfileSecretService(k8s_service=fake)
        service.sync_profile_secret(rp)
        service.ensure_none_secret()
        service.delete_profile_secret(rp.id)
        assert fake.upserts == []
        assert fake.deletes == []
    finally:
        db.close()


def test_apply_profile_save_upserts_secret_and_restarts_only_running_agents():
    db, owner, rp = _build_db()
    try:
        running = _make_agent(db, owner, rp, name="running", status="running")
        stopped = _make_agent(db, owner, rp, name="stopped", status="stopped")

        fake = _FakeK8s()
        service = RuntimeProfileSecretService(k8s_service=fake)
        result = service.apply_profile_save(db, rp)

        assert [name for name, _ in fake.upserts] == [f"efp-profile-{rp.id}"]
        assert fake.restarted == [running.id]
        assert result["bound_agent_count"] == 2
        assert result["running_agent_count"] == 1
        assert result["restarted_agent_ids"] == [running.id]
        assert result["failed_agent_ids"] == []

        db.refresh(running)
        db.refresh(stopped)
        assert running.status == "restarting"
        assert stopped.status == "stopped"
    finally:
        db.close()


def test_apply_profile_save_records_restart_failures():
    db, owner, rp = _build_db()
    try:
        running = _make_agent(db, owner, rp, name="running", status="running")

        fake = _FakeK8s(restart_status="failed")
        service = RuntimeProfileSecretService(k8s_service=fake)
        result = service.apply_profile_save(db, rp)

        assert result["failed_agent_ids"] == [running.id]
        assert result["restarted_agent_ids"] == []
        db.refresh(running)
        assert running.status == "running"
    finally:
        db.close()


def test_apply_profile_save_with_k8s_disabled_reports_counts_without_restarts():
    db, owner, rp = _build_db()
    try:
        _make_agent(db, owner, rp, name="running", status="running")

        fake = _FakeK8s(enabled=False)
        service = RuntimeProfileSecretService(k8s_service=fake)
        result = service.apply_profile_save(db, rp)

        assert fake.upserts == []
        assert fake.restarted == []
        assert result["running_agent_count"] == 1
        assert result["restarted_agent_ids"] == []
    finally:
        db.close()
