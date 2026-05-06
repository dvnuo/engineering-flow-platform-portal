from pathlib import Path

from app.models.agent import Agent


def test_0017_keeps_backfill_server_default_native():
    text = Path("alembic/versions/20260502_0017_agent_runtime_type_and_tools_repo.py").read_text(encoding="utf-8")
    assert 'server_default="native"' in text


def test_0018_drops_runtime_type_server_default_with_batch_alter_table():
    text = Path("alembic/versions/20260506_0018_drop_agents_runtime_type_server_default.py").read_text(encoding="utf-8")
    assert 'batch_alter_table("agents")' in text
    assert '"runtime_type"' in text
    assert 'server_default=None' in text


def test_agent_model_runtime_type_default_is_python_side_only():
    col = Agent.__table__.c.runtime_type
    assert col.server_default is None
    assert col.default is not None
