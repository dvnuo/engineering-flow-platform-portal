"""Stored Local browser connector rows become Local bridge rows."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text


def _alembic(database_url: str):
    from alembic.config import Config

    cfg = Config(str(Path("alembic.ini")))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _connector_types(engine) -> list[str]:
    with engine.connect() as conn:
        return [row.connector_type for row in conn.execute(text("SELECT connector_type FROM user_connectors ORDER BY id"))]


def test_upgrade_renames_local_browser_rows_and_downgrade_restores_them(tmp_path, monkeypatch):
    from alembic import command

    from app.config import get_settings

    database_url = f"sqlite:///{tmp_path / 'local-bridge.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        cfg = _alembic(database_url)
        command.upgrade(cfg, "20260928_0037")
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, role, is_active, created_at, updated_at) "
                    "VALUES (41, 'member', 'x', 'user', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            for row_id, connector_type in (("a", "local_browser"), ("b", "jira")):
                conn.execute(
                    text(
                        "INSERT INTO user_connectors (id, owner_user_id, connector_type, enabled, config_json, created_at, updated_at) "
                        "VALUES (:id, 41, :type, 1, '{\"preferred_port\": 8766}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"id": row_id, "type": connector_type},
                )

        command.upgrade(cfg, "20260930_0038")
        assert _connector_types(engine) == ["local_bridge", "jira"]
        with engine.connect() as conn:
            config_json = conn.execute(text("SELECT config_json FROM user_connectors WHERE id = 'a'")).scalar_one()
        assert "8766" in config_json

        command.downgrade(cfg, "20260928_0037")
        assert _connector_types(engine) == ["local_browser", "jira"]
    finally:
        get_settings.cache_clear()
