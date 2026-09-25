"""One settings row per member.

Every assistant uses its owner's single runtime profile. Saving a connector
bumps the row's revision only when something changed; idle running assistants
are restarted onto it, busy ones are left alone and flagged "restart to apply".
The 20260925_0036 migration collapses the several profiles a member used to
keep into that one row.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Agent, RuntimeProfile, User
from app.models.agent_execution import AgentExecution
from app.models.agent_task import AgentTask
from app.services.runtime_profile_secret_service import RuntimeProfileSecretService, profile_secret_name
from app.services.runtime_profile_service import RuntimeProfileService


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _user(db, username, role="user"):
    user = User(username=username, password_hash="x", role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _agent(db, name, owner_id, *, status="running", runtime_profile_id=None, profile_revision_applied=None, visibility="private"):
    agent = Agent(
        name=name,
        owner_user_id=owner_id,
        visibility=visibility,
        status=status,
        image="img",
        runtime_profile_id=runtime_profile_id,
        profile_revision_applied=profile_revision_applied,
        deployment_name=f"d-{name}",
        service_name=f"s-{name}",
        pvc_name=f"p-{name}",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


# ------------------------------------------------------------- save_config


def test_save_config_bumps_revision_only_when_the_config_changes():
    db = _session()
    user = _user(db, "u1")
    svc = RuntimeProfileService(db)
    profile = svc.get_or_create_for_user(user)
    start = profile.revision

    profile, changed = svc.save_config(profile, {"github": {"enabled": True, "api_token": "ghp_a"}})
    assert changed is True
    assert profile.revision == start + 1

    # The same settings again, as a dict or as JSON, are not a change.
    profile, changed = svc.save_config(profile, {"github": {"enabled": True, "api_token": "ghp_a"}})
    assert changed is False
    assert profile.revision == start + 1
    profile, changed = svc.save_config(profile, json.dumps({"github": {"api_token": "ghp_a", "enabled": True}}))
    assert changed is False
    assert profile.revision == start + 1

    # Neither is a difference the sanitizer throws away.
    profile, changed = svc.save_config(
        profile, {"github": {"enabled": True, "api_token": "ghp_a", "automation": {"mentions": {"enabled": True}}}}
    )
    assert changed is False
    assert profile.revision == start + 1

    profile, changed = svc.save_config(profile, {"github": {"enabled": True, "api_token": "ghp_b"}})
    assert changed is True
    assert profile.revision == start + 2
    db.expire_all()
    stored = db.get(RuntimeProfile, profile.id)
    assert stored.revision == start + 2
    assert json.loads(stored.config_json)["github"]["api_token"] == "ghp_b"


def test_get_or_create_for_user_returns_the_same_row():
    db = _session()
    user = _user(db, "u1")
    svc = RuntimeProfileService(db)

    first = svc.get_or_create_for_user(user)
    assert svc.get_or_create_for_user(user).id == first.id
    assert svc.ensure_user_has_default_profile(user).id == first.id
    assert svc.get_for_owner(user.id).id == first.id
    assert db.query(RuntimeProfile).filter_by(owner_user_id=user.id).count() == 1


def test_a_second_row_for_the_same_member_violates_the_unique_index():
    db = _session()
    user = _user(db, "u1")
    RuntimeProfileService(db).get_or_create_for_user(user)

    db.add(RuntimeProfile(owner_user_id=user.id, name="Another", config_json="{}"))
    with pytest.raises(IntegrityError):
        db.commit()


# ---------------------------------------------- ensure_defaults_for_all_users


def test_ensure_defaults_for_all_users_creates_one_row_per_user_and_binds_unassigned_agents():
    db = _session()
    alice = _user(db, "alice")
    bob = _user(db, "bob")
    carol = _user(db, "carol")

    # Bob already has his row; one of his assistants points at it, one at nothing.
    bobs = RuntimeProfile(owner_user_id=bob.id, name="Default", config_json='{"github": {"enabled": true}}', is_default=True)
    db.add(bobs)
    db.commit()
    db.refresh(bobs)
    bob_bound = _agent(db, "bob-bound", bob.id, runtime_profile_id=bobs.id)
    bob_loose = _agent(db, "bob-loose", bob.id)
    alice_loose = _agent(db, "alice-loose", alice.id)
    alice_loose_2 = _agent(db, "alice-loose-2", alice.id, status="stopped")

    svc = RuntimeProfileService(db)
    svc.ensure_defaults_for_all_users()

    rows = db.query(RuntimeProfile).all()
    assert sorted(row.owner_user_id for row in rows) == sorted([alice.id, bob.id, carol.id])

    db.expire_all()
    alice_row = svc.get_for_owner(alice.id)
    assert db.get(Agent, alice_loose.id).runtime_profile_id == alice_row.id
    assert db.get(Agent, alice_loose_2.id).runtime_profile_id == alice_row.id
    assert db.get(Agent, bob_loose.id).runtime_profile_id == bobs.id
    assert db.get(Agent, bob_bound.id).runtime_profile_id == bobs.id
    # Bob's existing row is kept as it was.
    assert json.loads(db.get(RuntimeProfile, bobs.id).config_json) == {"github": {"enabled": True}}

    # Running it again changes nothing.
    svc.ensure_defaults_for_all_users()
    assert db.query(RuntimeProfile).count() == 3


# --------------------------------------------------------------- migration


def _insert(conn, table, **values):
    columns = ", ".join(values)
    params = ", ".join(f":{key}" for key in values)
    conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({params})"), values)


def _insert_agent(conn, agent_id, owner_id, runtime_profile_id, now):
    _insert(
        conn,
        "agents",
        id=agent_id,
        name=agent_id,
        owner_user_id=owner_id,
        visibility="private",
        status="running",
        image="img",
        disk_size_gi=20,
        mount_path="/root/.efp",
        namespace="efp-agents",
        deployment_name=f"d-{agent_id}",
        service_name=f"s-{agent_id}",
        pvc_name=f"p-{agent_id}",
        agent_type="workspace",
        runtime_type="native",
        runtime_profile_id=runtime_profile_id,
        created_at=now,
        updated_at=now,
    )


def _insert_profile(conn, profile_id, owner_id, name, *, is_default, revision, updated_at):
    _insert(
        conn,
        "runtime_profiles",
        id=profile_id,
        owner_user_id=owner_id,
        name=name,
        description=f"{name} description",
        config_json=json.dumps({"note": name}),
        revision=revision,
        is_default=is_default,
        created_at="2026-01-01 00:00:00.000000",
        updated_at=updated_at,
    )


def test_migration_collapses_a_members_profiles_into_the_most_used_one(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from app.config import get_settings

    database_url = f"sqlite:///{tmp_path / 'collapse.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        alembic_cfg = Config(str(Path("alembic.ini")))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(alembic_cfg, "20260913_0035")

        engine = create_engine(database_url)
        now = "2026-09-01 00:00:00.000000"
        with engine.begin() as conn:
            for user_id, username in ((1, "member"), (2, "single"), (3, "profileless")):
                _insert(conn, "users", id=user_id, username=username, password_hash="x", role="user", is_active=True, created_at=now, updated_at=now)

            # The default is the most recently updated, but the most used wins.
            _insert_profile(conn, "p-default", 1, "Default", is_default=True, revision=3, updated_at="2026-09-20 00:00:00.000000")
            _insert_profile(conn, "p-bound", 1, "Work", is_default=False, revision=5, updated_at="2026-09-01 00:00:00.000000")
            _insert_profile(conn, "p-unbound", 1, "Spare", is_default=False, revision=2, updated_at="2026-09-10 00:00:00.000000")
            _insert_profile(conn, "p-single", 2, "Default", is_default=True, revision=4, updated_at=now)

            _insert_agent(conn, "a-bound-1", 1, "p-bound", now)
            _insert_agent(conn, "a-bound-2", 1, "p-bound", now)
            _insert_agent(conn, "a-on-default", 1, "p-default", now)
            _insert_agent(conn, "a-none", 1, None, now)
            _insert_agent(conn, "a-single", 2, "p-single", now)
            # Owner 3 has no profile of their own but the assistant points at
            # one of owner 1's rows that is about to be archived.
            _insert_agent(conn, "a-borrowed", 3, "p-unbound", now)

        command.upgrade(alembic_cfg, "20260925_0036")

        with engine.connect() as conn:
            profiles = {row.id: row for row in conn.execute(text("SELECT * FROM runtime_profiles"))}
            assert set(profiles) == {"p-bound", "p-single"}
            assert bool(profiles["p-bound"].is_default) is True
            assert profiles["p-bound"].revision == 5

            agents = {row.id: row for row in conn.execute(text("SELECT * FROM agents"))}
            for agent_id in ("a-bound-1", "a-bound-2", "a-on-default", "a-none"):
                assert agents[agent_id].runtime_profile_id == "p-bound", agent_id
            # Stayers already run the kept settings; movers still carry the old
            # ones until they restart.
            assert agents["a-bound-1"].profile_revision_applied == 5
            assert agents["a-bound-2"].profile_revision_applied == 5
            assert agents["a-on-default"].profile_revision_applied == 0
            assert agents["a-none"].profile_revision_applied == 0
            # A member with one profile is left as they were.
            assert agents["a-single"].runtime_profile_id == "p-single"
            assert agents["a-single"].profile_revision_applied == 4
            # Unbound rather than left pointing at a deleted row; startup gives
            # its owner a row and binds it.
            assert agents["a-borrowed"].runtime_profile_id is None
            assert agents["a-borrowed"].profile_revision_applied == 0

            archived = {row.id: row for row in conn.execute(text("SELECT * FROM runtime_profiles_archived"))}
            assert set(archived) == {"p-default", "p-unbound"}
            for profile_id, name, revision in (("p-default", "Default", 3), ("p-unbound", "Spare", 2)):
                row = archived[profile_id]
                assert row.merged_into_profile_id == "p-bound"
                assert row.owner_user_id == 1
                assert row.name == name
                assert row.revision == revision
                assert json.loads(row.config_json) == {"note": name}
                assert row.archived_at is not None

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                _insert_profile(conn, "p-again", 1, "Again", is_default=False, revision=1, updated_at=now)
        engine.dispose()
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------ save rollout


class _FakeK8s:
    enabled = True

    def __init__(self):
        self.secrets: dict[str, dict] = {}
        self.restarted: list[str] = []

    def upsert_secret(self, name, data):
        self.secrets[name] = data

    def restart_agent(self, agent):
        self.restarted.append(agent.id)
        return SimpleNamespace(status="restarting", message="Restart requested")


def test_apply_profile_save_restarts_idle_agents_and_leaves_busy_ones_pending():
    db = _session()
    user = _user(db, "u1")
    svc = RuntimeProfileService(db)
    profile = svc.get_or_create_for_user(user)
    profile, _ = svc.save_config(profile, {"github": {"enabled": True}})
    old = profile.revision - 1

    idle = _agent(db, "idle", user.id, runtime_profile_id=profile.id, profile_revision_applied=old)
    busy_task = _agent(db, "busy-task", user.id, runtime_profile_id=profile.id, profile_revision_applied=old)
    busy_chat = _agent(db, "busy-chat", user.id, runtime_profile_id=profile.id, profile_revision_applied=old)
    stale_chat = _agent(db, "stale-chat", user.id, runtime_profile_id=profile.id, profile_revision_applied=old)
    stopped = _agent(db, "stopped", user.id, status="stopped", runtime_profile_id=profile.id, profile_revision_applied=old)

    now = datetime.utcnow()
    db.add(AgentTask(assignee_agent_id=busy_task.id, source="test", task_type="t", status="queued"))
    # A finished task does not make an assistant busy.
    db.add(AgentTask(assignee_agent_id=idle.id, source="test", task_type="t", status="done"))
    db.add(AgentExecution(agent_id=busy_chat.id, kind="chat", status="running", created_at=now, updated_at=now - timedelta(minutes=5)))
    # A chat that has not reported for 3 hours was abandoned, not in progress.
    db.add(AgentExecution(agent_id=stale_chat.id, kind="chat", status="running", created_at=now - timedelta(hours=3), updated_at=now - timedelta(hours=3)))
    db.commit()

    k8s = _FakeK8s()
    result = RuntimeProfileSecretService(k8s_service=k8s).apply_profile_save(db, profile)

    assert profile_secret_name(profile.id) in k8s.secrets
    assert sorted(result["restarted_agent_ids"]) == sorted([idle.id, stale_chat.id])
    assert sorted(result["pending_agent_ids"]) == sorted([busy_task.id, busy_chat.id])
    assert result["failed_agent_ids"] == []
    assert sorted(k8s.restarted) == sorted([idle.id, stale_chat.id])
    assert result["bound_agent_count"] == 5
    assert result["running_agent_count"] == 4

    db.expire_all()
    for agent_id in (idle.id, stale_chat.id):
        agent = db.get(Agent, agent_id)
        assert agent.status == "restarting"
        assert agent.profile_revision_applied == profile.revision
    for agent_id in (busy_task.id, busy_chat.id):
        agent = db.get(Agent, agent_id)
        assert agent.status == "running"
        assert agent.profile_revision_applied == old
    untouched = db.get(Agent, stopped.id)
    assert untouched.status == "stopped"
    assert untouched.profile_revision_applied == old


@pytest.mark.parametrize("task_status", ["queued", "running", "pending_restart"])
def test_every_active_task_status_keeps_an_agent_busy(task_status):
    db = _session()
    user = _user(db, "u1")
    profile = RuntimeProfileService(db).get_or_create_for_user(user)
    agent = _agent(db, "a", user.id, runtime_profile_id=profile.id, profile_revision_applied=0)
    db.add(AgentTask(assignee_agent_id=agent.id, source="test", task_type="t", status=task_status))
    db.commit()

    k8s = _FakeK8s()
    result = RuntimeProfileSecretService(k8s_service=k8s).apply_profile_save(db, profile)

    assert result["pending_agent_ids"] == [agent.id]
    assert k8s.restarted == []


# ------------------------------------------------------ restart-pending flag


def _status_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    import app.api.agents as agents_api
    from app.services.runtime_status_cache import runtime_status_cache

    db = _session()
    owner = _user(db, "owner")
    other = _user(db, "other")
    mine = RuntimeProfile(owner_user_id=owner.id, name="Default", config_json="{}", revision=3, is_default=True)
    theirs = RuntimeProfile(owner_user_id=other.id, name="Default", config_json="{}", revision=3, is_default=True)
    db.add_all([mine, theirs])
    db.commit()

    agents = SimpleNamespace(
        behind=_agent(db, "behind", owner.id, runtime_profile_id=mine.id, profile_revision_applied=1),
        current=_agent(db, "current", owner.id, runtime_profile_id=mine.id, profile_revision_applied=3),
        unknown=_agent(db, "unknown", owner.id, runtime_profile_id=mine.id, profile_revision_applied=None),
        public=_agent(db, "public", other.id, runtime_profile_id=theirs.id, profile_revision_applied=1, visibility="public"),
    )

    monkeypatch.setattr(
        "app.api.agents.k8s_service.get_agent_runtime_status",
        lambda _agent: SimpleNamespace(status="running", message=None, cpu_usage=None, memory_usage=None),
    )

    async def _no_applied_revision(_agent):
        return None

    monkeypatch.setattr("app.api.agents._fetch_applied_profile_revision", _no_applied_revision)
    app.dependency_overrides[agents_api.get_current_user] = lambda: SimpleNamespace(
        id=owner.id, role="user", username="owner", nickname="owner"
    )
    app.dependency_overrides[agents_api.get_db] = lambda: db
    runtime_status_cache.clear()

    def cleanup():
        app.dependency_overrides.clear()
        runtime_status_cache.clear()
        db.close()

    return TestClient(app), agents, cleanup


def test_batch_status_reports_settings_restart_pending(monkeypatch):
    client, agents, cleanup = _status_client(monkeypatch)
    try:
        response = client.get("/api/agents/status")
        assert response.status_code == 200
        by_id = {row["id"]: row for row in response.json()["statuses"]}

        assert by_id[agents.behind.id]["settings_restart_pending"] is True
        assert by_id[agents.current.id]["settings_restart_pending"] is False
        assert by_id[agents.unknown.id]["settings_restart_pending"] is False
        # Readable but not the caller's to restart: never prompted.
        assert by_id[agents.public.id]["settings_restart_pending"] is False
    finally:
        cleanup()


def test_single_status_reports_settings_restart_pending(monkeypatch):
    client, agents, cleanup = _status_client(monkeypatch)
    try:
        expected = {
            agents.behind.id: True,
            agents.current.id: False,
            agents.unknown.id: False,
            agents.public.id: False,
        }
        for agent_id, pending in expected.items():
            response = client.get(f"/api/agents/{agent_id}/status")
            assert response.status_code == 200
            assert response.json()["settings_restart_pending"] is pending, agent_id
    finally:
        cleanup()
