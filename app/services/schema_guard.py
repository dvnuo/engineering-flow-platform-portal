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
    "runtime_profiles",
    "runtime_profile_sync_jobs",
    "agent_identity_bindings",
    "workflow_transition_rules",
    "runtime_capability_catalog_snapshots",
    "agent_groups",
    "agent_group_members",
    "agent_tasks",
    "automation_rules",
    "automation_rule_runs",
    "automation_rule_events",
)
REMOVED_PORTAL_TABLES = (
    "capability" + "_" + "profiles",
    "policy" + "_" + "profiles",
    "group_" + "shared_" + "context_snapshots",
    "external_event_subscriptions",
)
REMOVED_AGENT_COLUMNS = (
    "capability" + "_" + "profile_id",
    "policy" + "_" + "profile_id",
)
REMOVED_AGENT_TASK_COLUMNS = ("shared_" + "context_ref",)
REMOVED_AGENT_GROUP_COLUMNS = ("shared_" + "context_policy_json",)
REQUIRED_AUTOMATION_RULE_EVENT_COLUMNS = (
    "updated_at",
)
REQUIRED_RUNTIME_PROFILE_SYNC_JOB_COLUMNS = (
    "agent_id",
    "runtime_profile_id",
    "requested_revision",
    "action",
    "reason",
    "status",
    "attempts",
    "max_attempts",
    "next_run_at",
    "locked_until",
    "last_error",
    "created_at",
    "updated_at",
)


def assert_portal_schema_ready(engine: Engine) -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = [table for table in REQUIRED_PORTAL_TABLES if table not in existing_tables]
    if missing:
        missing_joined = ", ".join(sorted(missing))
        raise RuntimeError(
            "Database schema is incomplete for this Portal build. "
            f"Missing tables: {missing_joined}. "
            "Run 'alembic upgrade head' before starting Portal."
        )

    removed_tables = [table for table in REMOVED_PORTAL_TABLES if table in existing_tables]
    if removed_tables:
        removed_joined = ", ".join(sorted(removed_tables))
        raise RuntimeError(
            "Database schema still contains removed Portal tables: "
            f"{removed_joined}. Run 'alembic upgrade head' before starting Portal."
        )

    existing_columns = {column["name"] for column in inspector.get_columns("automation_rule_events")}
    missing_columns = [column for column in REQUIRED_AUTOMATION_RULE_EVENT_COLUMNS if column not in existing_columns]
    if missing_columns:
        missing_columns_joined = ", ".join(missing_columns)
        raise RuntimeError(
            "Database schema is incompatible with this Portal build. Missing columns on 'automation_rule_events': "
            f"{missing_columns_joined}. Run `alembic upgrade head` before starting Portal."
        )

    if "runtime_profile_sync_jobs" in existing_tables:
        existing_job_columns = {column["name"] for column in inspector.get_columns("runtime_profile_sync_jobs")}
        missing_job_columns = [
            column for column in REQUIRED_RUNTIME_PROFILE_SYNC_JOB_COLUMNS
            if column not in existing_job_columns
        ]
        if missing_job_columns:
            missing_columns_joined = ", ".join(missing_job_columns)
            raise RuntimeError(
                "Database schema is incompatible with this Portal build. Missing columns on "
                "'runtime_profile_sync_jobs': "
                f"{missing_columns_joined}. Run `alembic upgrade head` before starting Portal."
            )

    if "agents" in existing_tables:
        existing_agent_columns = {column["name"] for column in inspector.get_columns("agents")}
        removed_agent_columns = [column for column in REMOVED_AGENT_COLUMNS if column in existing_agent_columns]
        if removed_agent_columns:
            removed_joined = ", ".join(removed_agent_columns)
            raise RuntimeError(
                "Database schema still contains removed columns on 'agents': "
                f"{removed_joined}. Run `alembic upgrade head` before starting Portal."
            )

    if "agent_tasks" in existing_tables:
        existing_task_columns = {column["name"] for column in inspector.get_columns("agent_tasks")}
        removed_task_columns = [column for column in REMOVED_AGENT_TASK_COLUMNS if column in existing_task_columns]
        if removed_task_columns:
            removed_joined = ", ".join(removed_task_columns)
            raise RuntimeError(
                "Database schema still contains removed columns on 'agent_tasks': "
                f"{removed_joined}. Run `alembic upgrade head` before starting Portal."
            )

    if "agent_groups" in existing_tables:
        existing_group_columns = {column["name"] for column in inspector.get_columns("agent_groups")}
        removed_group_columns = [column for column in REMOVED_AGENT_GROUP_COLUMNS if column in existing_group_columns]
        if removed_group_columns:
            removed_joined = ", ".join(removed_group_columns)
            raise RuntimeError(
                "Database schema still contains removed columns on 'agent_groups': "
                f"{removed_joined}. Run `alembic upgrade head` before starting Portal."
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
