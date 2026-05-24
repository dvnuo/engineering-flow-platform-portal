from pathlib import Path


REQUIRED_NEW_TABLES = {
    "agent_groups",
    "agent_group_members",
    "agent_tasks",
    "capability_profiles",
    "policy_profiles",
    "workflow_transition_rules",
    "runtime_capability_catalog_snapshots",
    "agent_identity_bindings",
    "group_shared_context_snapshots",
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


def test_migrations_still_cover_existing_phase5_tables():
    source = _migration_source()
    for table_name in EXPECTED_ALREADY_COVERED:
        assert f'create_table("{table_name}"' in source
