"""The QA Assistant type is seeded next to Business, Dev, and Ops."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from app.services.assistant_type_icons import is_known_icon


def _alembic(database_url: str):
    from alembic.config import Config

    cfg = Config(str(Path("alembic.ini")))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _types(engine) -> dict[str, dict]:
    with engine.connect() as conn:
        return {row.id: dict(row._mapping) for row in conn.execute(text("SELECT * FROM assistant_types"))}


def test_upgrade_seeds_the_qa_type_on_the_qa_branches_and_downgrade_removes_it(tmp_path, monkeypatch):
    from alembic import command

    from app.config import get_settings

    database_url = f"sqlite:///{tmp_path / 'qa-type.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        cfg = _alembic(database_url)
        command.upgrade(cfg, "20260928_0038")
        engine = create_engine(database_url)

        types = _types(engine)
        assert {"business", "dev", "ops", "qa"} <= set(types)
        qa = types["qa"]
        assert qa["name"] == "QA Assistant"
        assert qa["agent_settings_branch"] == "qa" and qa["skill_branch"] == "qa"
        assert qa["runtime_type"] == "native" and bool(qa["is_active"])
        assert is_known_icon(qa["icon"])
        assert qa["sort_order"] > max(types[key]["sort_order"] for key in ("business", "dev", "ops"))

        command.downgrade(cfg, "20260927_0037")
        assert "qa" not in _types(engine)
        assert {"business", "dev", "ops"} <= set(_types(engine))
    finally:
        get_settings.cache_clear()


def test_upgrade_keeps_a_qa_type_an_administrator_already_made(tmp_path, monkeypatch):
    from alembic import command

    from app.config import get_settings

    database_url = f"sqlite:///{tmp_path / 'qa-type-manual.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        cfg = _alembic(database_url)
        command.upgrade(cfg, "20260927_0037")
        engine = create_engine(database_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO assistant_types (id, name, description, icon, runtime_type, agent_settings_branch, "
                    "skill_branch, sort_order, is_active, created_at, updated_at) VALUES "
                    "('manual-qa', 'QA Assistant', 'ours', 'bug', 'native', 'testing', 'testing', 5, 1, "
                    "'2026-09-01 00:00:00', '2026-09-01 00:00:00')"
                )
            )

        command.upgrade(cfg, "20260928_0038")

        types = _types(engine)
        assert "qa" not in types
        assert types["manual-qa"]["skill_branch"] == "testing"
    finally:
        get_settings.cache_clear()
