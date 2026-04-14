from sqlalchemy import inspect
from sqlalchemy.engine import Engine


REQUIRED_AGENT_COLUMNS = (
    "template_agent_id",
    "task_scope_label",
    "task_cleanup_policy",
    "runtime_profile_id",
)
REQUIRED_RUNTIME_PROFILE_COLUMNS = (
    "owner_user_id",
    "is_default",
)
REQUIRED_PORTAL_TABLES = (
    "alembic_version",
    "users",
    "agents",
    "audit_logs",
    "agent_delegations",
    "agent_coordination_runs",
    "agent_session_metadata",
    "capability_profiles",
    "policy_profiles",
    "runtime_profiles",
    "agent_identity_bindings",
    "workflow_transition_rules",
    "external_event_subscriptions",
    "runtime_capability_catalog_snapshots",
    "agent_groups",
    "agent_group_members",
    "agent_tasks",
    "group_shared_context_snapshots",
)


def assert_portal_schema_ready(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = [table for table in REQUIRED_PORTAL_TABLES if table not in existing_tables]
    if not missing:
        return
    missing_joined = ", ".join(sorted(missing))
    raise RuntimeError(
        "Database schema is incomplete for this Portal build. "
        f"Missing tables: {missing_joined}. "
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


def assert_runtime_profile_schema_compatibility(engine: Engine) -> None:
    inspector = inspect(engine)
    if "runtime_profiles" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("runtime_profiles")}
    missing = [column for column in REQUIRED_RUNTIME_PROFILE_COLUMNS if column not in existing_columns]
    if not missing:
        return

    missing_joined = ", ".join(missing)
    raise RuntimeError(
        "Database schema is incompatible with this Portal build. Missing columns on 'runtime_profiles': "
        f"{missing_joined}. Run `alembic upgrade head` before starting Portal."
    )
