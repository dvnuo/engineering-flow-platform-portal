from pathlib import Path


REQUIRED_NEW_TABLES = {
    "agent_groups",
    "agent_group_members",
    "agent_tasks",
    "workflow_transition_rules",
    "runtime_capability_catalog_snapshots",
    "agent_identity_bindings",
}
REMOVED_TABLES = {
    "capability" + "_" + "profiles",
    "policy" + "_" + "profiles",
    "group_" + "shared_" + "context_snapshots",
    "external_event_subscriptions",
}

EXPECTED_ALREADY_COVERED = {
    "agent_coordination_runs",
    "agent_session_metadata",
}


def _migration_source() -> str:
    versions_dir = Path("alembic/versions")
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(versions_dir.glob("*.py")))
    source = source.replace("'", "\"")
    return "".join(source.split())


def test_migrations_cover_control_plane_tables():
    source = _migration_source()
    for table_name in REQUIRED_NEW_TABLES:
        assert f'create_table("{table_name}"' in source


def test_migrations_do_not_recreate_removed_control_planes():
    source = _migration_source()
    for table_name in REMOVED_TABLES:
        assert f'create_table("{table_name}"' not in source


def test_migrations_still_cover_existing_phase5_tables():
    source = _migration_source()
    for table_name in EXPECTED_ALREADY_COVERED:
        assert f'create_table("{table_name}"' in source


def test_obsolete_agent_task_context_column_is_dropped_not_recreated():
    obsolete_column = "bundle_id"
    old_revision = Path("alembic/versions/20260414_0011_add_triggered_work_subscription_fields.py").read_text(encoding="utf-8")
    drop_revision = Path("alembic/versions/20260524_0023_drop_agent_task_bundle_id.py").read_text(encoding="utf-8")
    assert f'op.add_column("agent_tasks", sa.Column("{obsolete_column}"' not in old_revision
    assert f'drop_column("{obsolete_column}")' in drop_revision
