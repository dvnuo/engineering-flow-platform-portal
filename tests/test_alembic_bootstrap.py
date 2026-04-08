from pathlib import Path


def test_env_runs_standard_migrations_without_create_all_shortcut():
    source = Path("alembic/env.py").read_text(encoding="utf-8")
    assert "Base.metadata.create_all" not in source
    assert "should_bootstrap_empty_db" not in source


def test_initial_baseline_migration_exists_and_is_root():
    baseline_source = Path("alembic/versions/20260407_0000_initial_portal_schema.py").read_text(encoding="utf-8")
    assert 'revision = "20260407_0000"' in baseline_source
    assert "down_revision = None" in baseline_source
    for table_name in ["users", "agents", "audit_logs", "agent_delegations"]:
        assert f'"{table_name}",' in baseline_source

    delegation_source = Path("alembic/versions/20260407_0001_add_delegation_routing_intent.py").read_text(encoding="utf-8")
    assert 'down_revision = "20260407_0000"' in delegation_source
