from sqlalchemy import Column, Integer, String, Table, MetaData, create_engine

from app.services.schema_guard import assert_phase5_schema_compatibility, assert_portal_schema_ready

REQUIRED_PORTAL_TABLES = (
    "alembic_version",
    "users",
    "agents",
    "audit_logs",
    "agent_session_metadata",
    "runtime_profiles",
    "runtime_profile_sync_jobs",
    "runtime_capability_catalog_snapshots",
    "agent_tasks",
    "automation_rules",
    "automation_rule_runs",
    "automation_rule_events",
)


def _runtime_profile_sync_jobs_table(metadata: MetaData, *, minimal: bool = False):
    if minimal:
        return Table("runtime_profile_sync_jobs", metadata, Column("id", String(36), primary_key=True))
    return Table(
        "runtime_profile_sync_jobs",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("agent_id", String(36)),
        Column("runtime_profile_id", String(36)),
        Column("requested_revision", Integer),
        Column("action", String(16)),
        Column("reason", String(128)),
        Column("status", String(32)),
        Column("attempts", Integer),
        Column("max_attempts", Integer),
        Column("next_run_at", String(32)),
        Column("locked_until", String(32)),
        Column("last_error", String(255)),
        Column("created_at", String(32)),
        Column("updated_at", String(32)),
    )


def test_schema_guard_passes_when_agents_table_has_required_columns():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "agents",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("runtime_profile_id", String(36)),
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
        elif table_name == "automation_rule_events":
            Table(table_name, metadata, Column("id", String(36), primary_key=True), Column("updated_at", String(32)))
        elif table_name == "runtime_profile_sync_jobs":
            _runtime_profile_sync_jobs_table(metadata)
        else:
            Table(table_name, metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)

    assert_portal_schema_ready(engine)


def test_portal_schema_ready_requires_runtime_profile_sync_jobs_table():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    for table_name in REQUIRED_PORTAL_TABLES:
        if table_name == "runtime_profile_sync_jobs":
            continue
        if table_name == "alembic_version":
            Table(table_name, metadata, Column("version_num", String(32), primary_key=True))
        elif table_name == "users":
            Table(table_name, metadata, Column("id", Integer, primary_key=True))
        elif table_name == "automation_rule_events":
            Table(table_name, metadata, Column("id", String(36), primary_key=True), Column("updated_at", String(32)))
        else:
            Table(table_name, metadata, Column("id", String(36), primary_key=True))

    metadata.create_all(engine)

    try:
        assert_portal_schema_ready(engine)
    except RuntimeError as exc:
        message = str(exc)
        assert "runtime_profile_sync_jobs" in message
        assert "alembic upgrade head" in message
    else:
        raise AssertionError("expected RuntimeError for missing runtime_profile_sync_jobs table")


def test_portal_schema_ready_rejects_runtime_profile_sync_jobs_missing_columns():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    for table_name in REQUIRED_PORTAL_TABLES:
        if table_name == "alembic_version":
            Table(table_name, metadata, Column("version_num", String(32), primary_key=True))
        elif table_name == "users":
            Table(table_name, metadata, Column("id", Integer, primary_key=True))
        elif table_name == "automation_rule_events":
            Table(table_name, metadata, Column("id", String(36), primary_key=True), Column("updated_at", String(32)))
        elif table_name == "runtime_profile_sync_jobs":
            _runtime_profile_sync_jobs_table(metadata, minimal=True)
        else:
            Table(table_name, metadata, Column("id", String(36), primary_key=True))

    metadata.create_all(engine)

    try:
        assert_portal_schema_ready(engine)
    except RuntimeError as exc:
        message = str(exc)
        assert "runtime_profile_sync_jobs" in message
        assert "agent_id" in message
        assert "status" in message
        assert "alembic upgrade head" in message
    else:
        raise AssertionError("expected RuntimeError for incomplete runtime_profile_sync_jobs table")
