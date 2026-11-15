"""Migration 0048 and the models of the Apps feature."""

from datetime import date, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import UniqueConstraint, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

APP_TABLES = (
    "apps",
    "app_releases",
    "app_template_versions",
    "app_executions",
    "app_traffic_daily",
    "app_visits",
    "app_audit_events",
)
ROLLUP_DIMS = ["day", "user_id", "agent_id", "app_id", "runtime_type", "kind", "model"]


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    """A fresh SQLite file brought to head by the migrations alone."""
    from app.config import get_settings

    database_url = f"sqlite:///{(tmp_path / 'apps.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path("alembic.ini")))
    config.set_main_option("sqlalchemy.url", database_url)
    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            yield engine, config
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def test_migration_creates_the_app_tables_and_the_schema_guard_passes(migrated):
    from app.services.schema_guard import REQUIRED_PORTAL_TABLES, assert_portal_schema_ready

    engine, _config = migrated
    tables = set(inspect(engine).get_table_names())
    assert set(APP_TABLES) <= tables
    assert set(APP_TABLES) <= set(REQUIRED_PORTAL_TABLES)
    assert_portal_schema_ready(engine)


def test_migrated_schema_is_the_models_schema(migrated):
    from app import models  # noqa: F401
    from app.db import Base

    engine, _config = migrated
    inspector = inspect(engine)
    for name in (*APP_TABLES, "usage_daily_rollups"):
        table = Base.metadata.tables[name]
        assert {column["name"] for column in inspector.get_columns(name)} == {column.name for column in table.columns}, name
        assert {index["name"] for index in inspector.get_indexes(name)} == {index.name for index in table.indexes}, name
        modeled = {item.name for item in table.constraints if isinstance(item, UniqueConstraint)}
        assert {item["name"] for item in inspector.get_unique_constraints(name)} == modeled, name


def test_rollups_take_an_app_dimension_with_the_empty_sentinel(migrated):
    engine, _config = migrated
    inspector = inspect(engine)
    column = next(item for item in inspector.get_columns("usage_daily_rollups") if item["name"] == "app_id")
    assert column["nullable"] is False
    constraint = next(item for item in inspector.get_unique_constraints("usage_daily_rollups") if item["name"] == "uq_usage_daily_rollups_dims")
    assert constraint["column_names"] == ROLLUP_DIMS
    # The indexes the table had survive SQLite's rebuild.
    assert {index["name"] for index in inspector.get_indexes("usage_daily_rollups")} == {
        "ix_usage_daily_rollups_day",
        "ix_usage_daily_rollups_user_day",
    }

    from app.models import UsageDailyRollup

    with Session(engine) as db:
        # An assistant's row is written without naming an app, as before.
        db.add(UsageDailyRollup(day=date(2026, 11, 15), user_id=1, agent_id="agent-1", runtime_type="native", kind="chat", model="gpt"))
        # Two apps of one member on the same day and model are two rows.
        db.add(UsageDailyRollup(day=date(2026, 11, 15), user_id=1, app_id="app-a", runtime_type="app", kind="llm", model="gpt"))
        db.add(UsageDailyRollup(day=date(2026, 11, 15), user_id=1, app_id="app-b", runtime_type="app", kind="llm", model="gpt"))
        db.commit()
        assert db.execute(text("SELECT app_id FROM usage_daily_rollups WHERE agent_id = 'agent-1'")).scalar() == ""
        db.add(UsageDailyRollup(day=date(2026, 11, 15), user_id=1, app_id="app-a", runtime_type="app", kind="llm", model="gpt"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_existing_rollup_rows_survive_the_migration(tmp_path, monkeypatch):
    from app.config import get_settings

    database_url = f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(Path("alembic.ini")))
    config.set_main_option("sqlalchemy.url", database_url)
    try:
        command.upgrade(config, "20261110_0047")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO usage_daily_rollups (day, user_id, agent_id, runtime_type, kind, model, executions, updated_at) "
                    "VALUES ('2026-11-01', 7, 'agent-9', 'native', 'chat', 'gpt', 3, '2026-11-01 00:00:00')"
                )
            )
        engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            row = connection.execute(text("SELECT user_id, agent_id, app_id, executions FROM usage_daily_rollups")).one()
        assert tuple(row) == (7, "agent-9", "", 3)
        engine.dispose()

        # And back: the tables go, the constraint returns to its six columns.
        command.downgrade(config, "20261110_0047")
        engine = create_engine(database_url)
        inspector = inspect(engine)
        assert not set(APP_TABLES) & set(inspector.get_table_names())
        assert "app_id" not in {column["name"] for column in inspector.get_columns("usage_daily_rollups")}
        constraint = next(item for item in inspector.get_unique_constraints("usage_daily_rollups") if item["name"] == "uq_usage_daily_rollups_dims")
        assert constraint["column_names"] == [name for name in ROLLUP_DIMS if name != "app_id"]
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_slug_and_token_hash_are_unique_including_deleted_apps():
    from _apps_helpers import add_user, make_database
    from app.models import App

    factory, _engine = make_database()
    db = factory()
    owner = add_user(db, "owner")

    def app(slug, token_hash, **extra):
        return App(
            slug=slug, name=slug, owner_user_id=owner.id, repo_url="https://github.com/a/b", repo_ref="main",
            token_hash=token_hash, token_value="efpa_x", visitor_key="k", **extra,
        )

    db.add(app("mindmap", "h1", deleted_at=datetime.utcnow()))
    db.commit()
    db.add(app("mindmap", "h2"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.add(app("other", "h1"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    stored = db.query(App).one()
    assert stored.env == {} and stored.connectors_enabled == [] and stored.connectors_write == []
    assert stored.llm_enabled is False and stored.visibility == "private" and stored.desired_state == "running"
    assert stored.is_deleted is True
    db.close()
