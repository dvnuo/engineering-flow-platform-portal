from sqlalchemy import inspect
from sqlalchemy.engine import Engine


REQUIRED_AGENT_COLUMNS = (
    "template_agent_id",
    "task_scope_label",
    "task_cleanup_policy",
)


def assert_phase5_schema_compatibility(engine: Engine) -> None:
    inspector = inspect(engine)
    if "agents" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("agents")}
    missing = [column for column in REQUIRED_AGENT_COLUMNS if column not in existing_columns]
    if not missing:
        return

    missing_joined = ", ".join(missing)
    raise RuntimeError(
        "Database schema is incompatible with this Portal build. Missing columns on 'agents': "
        f"{missing_joined}. Run `alembic upgrade head` before starting Portal."
    )

