from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    inspect,
    select,
)


def test_alembic_upgrade_head_adopts_legacy_master_sqlite_db(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-master.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url)
    metadata = MetaData()

    users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True, nullable=False),
        Column("username", String(64), nullable=False, unique=True),
        Column("nickname", String(64), nullable=True),
        Column("password_hash", String(255), nullable=False),
        Column("role", String(16), nullable=False),
        Column("is_active", Boolean, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    agents = Table(
        "agents",
        metadata,
        Column("id", String(36), primary_key=True, nullable=False),
        Column("name", String(128), nullable=False),
        Column("description", Text, nullable=True),
        Column("owner_user_id", Integer, nullable=False),
        Column("visibility", String(16), nullable=False),
        Column("status", String(16), nullable=False),
        Column("image", String(255), nullable=False),
        Column("repo_url", String(512), nullable=True),
        Column("branch", String(128), nullable=True),
        Column("cpu", String(32), nullable=True),
        Column("memory", String(32), nullable=True),
        Column("disk_size_gi", Integer, nullable=False),
        Column("mount_path", String(255), nullable=False),
        Column("namespace", String(63), nullable=False),
        Column("deployment_name", String(128), nullable=False),
        Column("service_name", String(128), nullable=False),
        Column("pvc_name", String(128), nullable=False),
        Column("endpoint_path", String(255), nullable=True),
        Column("last_error", Text, nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    Table(
        "audit_logs",
        metadata,
        Column("id", Integer, primary_key=True, nullable=False),
        Column("user_id", Integer, nullable=True),
        Column("action", String(128), nullable=False),
        Column("target_type", String(32), nullable=False),
        Column("target_id", String(64), nullable=False),
        Column("details_json", Text, nullable=True),
        Column("created_at", DateTime, nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        legacy_timestamp = datetime(2026, 1, 1, 0, 0, 0)
        connection.execute(
            users.insert().values(
                id=1,
                username="legacy_admin",
                nickname="Legacy",
                password_hash="hash",
                role="admin",
                is_active=True,
                created_at=legacy_timestamp,
                updated_at=legacy_timestamp,
            )
        )
        connection.execute(
            agents.insert().values(
                id="legacy-agent-1",
                name="Legacy Agent",
                description="legacy row",
                owner_user_id=1,
                visibility="private",
                status="ready",
                image="python:3.11",
                repo_url="https://example.invalid/repo.git",
                branch="main",
                cpu="1",
                memory="1Gi",
                disk_size_gi=10,
                mount_path="/workspace",
                namespace="legacy-ns",
                deployment_name="legacy-deploy",
                service_name="legacy-svc",
                pvc_name="legacy-pvc",
                endpoint_path=None,
                last_error=None,
                created_at=legacy_timestamp,
                updated_at=legacy_timestamp,
            )
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    alembic_cfg = Config(str(Path("alembic.ini")))
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_cfg, "head")

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    assert "users" in existing_tables
    assert "agents" in existing_tables
    assert "audit_logs" in existing_tables
    assert "agent_delegations" in existing_tables

    for table_name in {
        "capability_profiles",
        "policy_profiles",
        "agent_identity_bindings",
        "agent_tasks",
        "agent_groups",
        "agent_group_members",
        "group_shared_context_snapshots",
        "workflow_transition_rules",
        "external_event_subscriptions",
        "runtime_capability_catalog_snapshots",
        "agent_coordination_runs",
        "agent_session_metadata",
        "alembic_version",
    }:
        assert table_name in existing_tables

    agent_columns = {column["name"] for column in inspector.get_columns("agents")}
    assert "agent_type" in agent_columns
    assert "capability_profile_id" in agent_columns
    assert "policy_profile_id" in agent_columns

    migrated_agents = Table("agents", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        agent_type = connection.execute(
            select(migrated_agents.c.agent_type).where(migrated_agents.c.id == "legacy-agent-1")
        ).scalar_one()
    assert agent_type is not None
    assert agent_type == "workspace"
