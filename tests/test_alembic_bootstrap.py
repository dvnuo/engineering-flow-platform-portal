import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_env_runs_standard_migrations_without_create_all_shortcut():
    source = Path("alembic/env.py").read_text(encoding="utf-8")
    assert "Base.metadata.create_all" not in source
    assert "alembic_bootstrap" not in source


def test_env_logging_setup_does_not_disable_existing_app_loggers(tmp_path, monkeypatch):
    """env.py imports the app package (instantiating every `app.*` logger) and
    then hands alembic.ini to fileConfig; unless it opts out, fileConfig
    disables every logger it does not name, so run a real in-process upgrade and
    check an already-existing app logger still logs afterwards."""
    from app.config import get_settings

    probe = logging.getLogger("app.alembic_fileconfig_probe")
    probe.disabled = False
    database_url = f"sqlite:///{tmp_path / 'logging.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        alembic_cfg = Config(str(Path("alembic.ini")))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(alembic_cfg, "head")

        assert probe.disabled is False
        assert probe.isEnabledFor(logging.ERROR)
    finally:
        probe.disabled = False
        get_settings.cache_clear()


def test_initial_baseline_migration_exists_and_is_root():
    baseline_source = Path("alembic/versions/20260407_0000_initial_portal_schema.py").read_text(encoding="utf-8")
    assert 'revision = "20260407_0000"' in baseline_source
    assert "down_revision = None" in baseline_source
    for table_name in ["users", "agents", "audit_logs"]:
        assert f'"{table_name}",' in baseline_source

    next_source = Path("alembic/versions/20260407_0001_neutral_control_checkpoint.py").read_text(encoding="utf-8")
    assert 'down_revision = "20260407_0000"' in next_source


def test_alembic_upgrade_head_bootstraps_delegation_tables(tmp_path, monkeypatch):
    from app.config import get_settings

    db_path = tmp_path / "bootstrap.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    alembic_cfg = Config(str(Path("alembic.ini")))
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"delegation_rules", "delegation_rule_runs", "delegation_rule_events"}.issubset(tables)
    assert "automation_rules" not in tables
    assert "automation_rule_runs" not in tables
    assert "automation_rule_events" not in tables

    delegation_indexes = {index["name"] for index in inspector.get_indexes("delegation_rules")}
    assert "ix_delegation_rules_enabled_next_run_at" in delegation_indexes
    event_unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("delegation_rule_events")}
    assert "uq_delegation_rule_events_rule_dedupe" in event_unique_constraints

    get_settings.cache_clear()
