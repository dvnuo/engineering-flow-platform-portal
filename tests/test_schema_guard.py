from sqlalchemy import Column, Integer, String, Table, MetaData, create_engine

from app.services.schema_guard import assert_phase5_schema_compatibility


def test_schema_guard_passes_when_agents_table_has_required_columns():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "agents",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("template_agent_id", String(36)),
        Column("task_scope_label", String(255)),
        Column("task_cleanup_policy", String(32)),
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
        assert "template_agent_id" in message
        assert "task_scope_label" in message
        assert "task_cleanup_policy" in message
        assert "alembic upgrade head" in message
    else:
        raise AssertionError("expected RuntimeError for missing phase5 agent columns")

