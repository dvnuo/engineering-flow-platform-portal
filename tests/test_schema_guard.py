from sqlalchemy import Column, Integer, String, Table, MetaData, create_engine

from app.services.schema_guard import assert_phase5_schema_compatibility, assert_portal_schema_ready

REQUIRED_PORTAL_TABLES = (
    "alembic_version",
    "users",
    "agents",
    "audit_logs",
    "agent_session_metadata",
    "runtime_profiles",
    "runtime_capability_catalog_snapshots",
    "agent_tasks",
    "agent_executions",
    "delegation_rules",
    "delegation_rule_runs",
    "delegation_rule_events",
)


def test_schema_guard_passes_when_agents_table_has_required_columns():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "agents",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("runtime_profile_id", String(36)),
        Column("agent_settings_repo_url", String(512)),
        Column("agent_settings_branch", String(128)),
        Column("agent_settings_subdir", String(255)),
    )
    metadata.create_all(engine)

    assert_phase5_schema_compatibility(engine)


def test_schema_guard_passes_when_agents_table_missing_on_brand_new_db():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)

    assert_phase5_schema_compatibility(engine)


def test_schema_guard_raises_with_actionable_message_when_columns_missing():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("agents", metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)

    try:
        assert_phase5_schema_compatibility(engine)
    except RuntimeError as exc:
        message = str(exc)
        assert "runtime_profile_id" in message
        assert "alembic upgrade head" in message
    else:
        raise AssertionError("expected RuntimeError for missing agent columns")


def test_portal_schema_ready_raises_when_required_tables_missing():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)

    try:
        assert_portal_schema_ready(engine)
    except RuntimeError as exc:
        message = str(exc)
        assert "Database schema is incomplete" in message
        assert "agents" in message
        assert "alembic_version" in message
        assert "alembic upgrade head" in message
    else:
        raise AssertionError("expected RuntimeError for missing required portal tables")


def test_portal_schema_ready_passes_with_all_required_tables():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    for table_name in REQUIRED_PORTAL_TABLES:
        if table_name == "alembic_version":
            Table(table_name, metadata, Column("version_num", String(32), primary_key=True))
        elif table_name == "users":
            Table(table_name, metadata, Column("id", Integer, primary_key=True))
        elif table_name == "delegation_rule_events":
            Table(table_name, metadata, Column("id", String(36), primary_key=True), Column("updated_at", String(32)))
        else:
            Table(table_name, metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)

    assert_portal_schema_ready(engine)


def test_portal_schema_guard_requires_delegation_tables_not_automation_tables():
    assert "delegation_rules" in REQUIRED_PORTAL_TABLES
    assert "delegation_rule_runs" in REQUIRED_PORTAL_TABLES
    assert "delegation_rule_events" in REQUIRED_PORTAL_TABLES
    assert "automation_rules" not in REQUIRED_PORTAL_TABLES
    assert "automation_rule_runs" not in REQUIRED_PORTAL_TABLES
    assert "automation_rule_events" not in REQUIRED_PORTAL_TABLES


def test_portal_schema_guard_does_not_require_retired_sync_jobs_table():
    from app.services.schema_guard import REQUIRED_PORTAL_TABLES as GUARD_TABLES

    assert "runtime_profile_sync_jobs" not in GUARD_TABLES
