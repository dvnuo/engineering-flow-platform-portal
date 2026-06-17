from pathlib import Path


REQUIRED_NEW_TABLES = {
    "agent_executions",
    "agent_tasks",
    "runtime_capability_catalog_snapshots",
}


def _migration_source() -> str:
    versions_dir = Path("alembic/versions")
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(versions_dir.glob("*.py")))
    source = source.replace("'", "\"")
    return "".join(source.split())


def test_migrations_cover_active_tables():
    source = _migration_source()
    for table_name in REQUIRED_NEW_TABLES:
        assert f'create_table("{table_name}"' in source


def test_obsolete_agent_task_context_column_is_dropped_not_recreated():
    obsolete_column = "bundle_id"
    old_revision = Path("alembic/versions/20260414_0011_add_agent_task_trigger_metadata.py").read_text(encoding="utf-8")
    drop_revision = Path("alembic/versions/20260524_0023_drop_agent_task_bundle_id.py").read_text(encoding="utf-8")
    assert f'op.add_column("agent_tasks", sa.Column("{obsolete_column}"' not in old_revision
    assert f'drop_column("{obsolete_column}")' in drop_revision


def test_agent_task_list_indexes_are_migrated_and_modeled():
    migration = Path("alembic/versions/20260617_0028_add_agent_task_list_indexes.py").read_text(encoding="utf-8")
    model = Path("app/models/agent_task.py").read_text(encoding="utf-8")

    for index_name in [
        "ix_agent_tasks_updated_created_id",
        "ix_agent_tasks_status_updated_created_id",
        "ix_agent_tasks_owner_updated_created_id",
        "ix_agent_tasks_owner_status_updated_created_id",
    ]:
        assert index_name in migration
        assert index_name in model
