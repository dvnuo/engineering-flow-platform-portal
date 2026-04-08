from sqlalchemy import inspect
from sqlalchemy.engine import Engine


REQUIRED_AGENT_COLUMNS = (
    "template_agent_id",
    "task_scope_label",
    "task_cleanup_policy",
)
REQUIRED_PORTAL_TABLES = (
    "alembic_version",
    "users",
    "agents",
)


def assert_portal_schema_ready(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = [table for table in REQUIRED_PORTAL_TABLES if table not in existing_tables]
    if not missing:
        return
    raise RuntimeError(
        "Database is not initialized for this Portal build. "
        "Run 'alembic upgrade head' before starting Portal."
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
