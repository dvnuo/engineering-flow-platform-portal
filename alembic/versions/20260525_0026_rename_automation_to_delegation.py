"""rename automation rules to delegation rules

Revision ID: 20260525_0026
Revises: 20260524_0025
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260525_0026"
down_revision = "20260524_0025"
branch_labels = None
depends_on = None


RULE_COLUMNS = [
    "id",
    "name",
    "enabled",
    "source_type",
    "trigger_type",
    "target_agent_id",
    "task_type",
    "scope_json",
    "trigger_config_json",
    "task_config_json",
    "schedule_json",
    "state_json",
    "last_run_at",
    "next_run_at",
    "locked_until",
    "owner_user_id",
    "created_by_user_id",
    "created_at",
    "updated_at",
]
RUN_COLUMNS = [
    "id",
    "rule_id",
    "status",
    "started_at",
    "finished_at",
    "found_count",
    "created_task_count",
    "skipped_count",
    "error_message",
    "metrics_json",
]
EVENT_COLUMNS = [
    "id",
    "rule_id",
    "dedupe_key",
    "status",
    "source_payload_json",
    "normalized_payload_json",
    "task_id",
    "error_message",
    "created_at",
    "updated_at",
]


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in _inspector().get_indexes(table_name))


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _copy_if_source_exists(source_table: str, target_table: str, columns: list[str]) -> None:
    if not _has_table(source_table) or not _has_table(target_table):
        return
    column_sql = ", ".join(columns)
    op.execute(sa.text(f"INSERT INTO {target_table} ({column_sql}) SELECT {column_sql} FROM {source_table}"))


def _drop_table_if_exists(table_name: str) -> None:
    if _has_table(table_name):
        op.drop_table(table_name)


def _create_delegation_tables() -> None:
    if not _has_table("delegation_rules"):
        op.create_table(
            "delegation_rules",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("source_type", sa.String(length=64), nullable=False),
            sa.Column("trigger_type", sa.String(length=128), nullable=False),
            sa.Column("target_agent_id", sa.String(length=36), nullable=False),
            sa.Column("task_type", sa.String(length=128), nullable=False),
            sa.Column("scope_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("trigger_config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("task_config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("schedule_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("state_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("locked_until", sa.DateTime(), nullable=True),
            sa.Column("owner_user_id", sa.Integer(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["target_agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_delegation_rules_enabled_next_run_at", "delegation_rules", ["enabled", "next_run_at"])
    _create_index_if_missing(
        "ix_delegation_rules_source_trigger_enabled",
        "delegation_rules",
        ["source_type", "trigger_type", "enabled"],
    )
    _create_index_if_missing("ix_delegation_rules_target_agent_enabled", "delegation_rules", ["target_agent_id", "enabled"])
    _create_index_if_missing("ix_delegation_rules_owner_user_id", "delegation_rules", ["owner_user_id"])

    if not _has_table("delegation_rule_runs"):
        op.create_table(
            "delegation_rule_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("rule_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_task_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
            sa.ForeignKeyConstraint(["rule_id"], ["delegation_rules.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_delegation_rule_runs_rule_id", "delegation_rule_runs", ["rule_id"])

    if not _has_table("delegation_rule_events"):
        op.create_table(
            "delegation_rule_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("rule_id", sa.String(length=36), nullable=False),
            sa.Column("dedupe_key", sa.String(length=512), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("normalized_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("task_id", sa.String(length=36), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["rule_id"], ["delegation_rules.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("rule_id", "dedupe_key", name="uq_delegation_rule_events_rule_dedupe"),
        )
    _create_index_if_missing("ix_delegation_rule_events_rule_id", "delegation_rule_events", ["rule_id"])
    _create_index_if_missing("ix_delegation_rule_events_task_id", "delegation_rule_events", ["task_id"])


def _create_automation_tables() -> None:
    if not _has_table("automation_rules"):
        op.create_table(
            "automation_rules",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("source_type", sa.String(length=64), nullable=False),
            sa.Column("trigger_type", sa.String(length=128), nullable=False),
            sa.Column("target_agent_id", sa.String(length=36), nullable=False),
            sa.Column("task_type", sa.String(length=128), nullable=False),
            sa.Column("scope_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("trigger_config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("task_config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("schedule_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("state_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("next_run_at", sa.DateTime(), nullable=True),
            sa.Column("locked_until", sa.DateTime(), nullable=True),
            sa.Column("owner_user_id", sa.Integer(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["target_agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_automation_rules_enabled_next_run_at", "automation_rules", ["enabled", "next_run_at"])
    _create_index_if_missing(
        "ix_automation_rules_source_trigger_enabled",
        "automation_rules",
        ["source_type", "trigger_type", "enabled"],
    )
    _create_index_if_missing("ix_automation_rules_target_agent_enabled", "automation_rules", ["target_agent_id", "enabled"])

    if not _has_table("automation_rule_runs"):
        op.create_table(
            "automation_rule_runs",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("rule_id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_task_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("metrics_json", sa.Text(), nullable=False, server_default="{}"),
            sa.ForeignKeyConstraint(["rule_id"], ["automation_rules.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    _create_index_if_missing("ix_automation_rule_runs_rule_id", "automation_rule_runs", ["rule_id"])

    if not _has_table("automation_rule_events"):
        op.create_table(
            "automation_rule_events",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("rule_id", sa.String(length=36), nullable=False),
            sa.Column("dedupe_key", sa.String(length=512), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("normalized_payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("task_id", sa.String(length=36), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["rule_id"], ["automation_rules.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("rule_id", "dedupe_key", name="uq_automation_rule_events_rule_dedupe"),
        )
    _create_index_if_missing("ix_automation_rule_events_rule_id", "automation_rule_events", ["rule_id"])
    _create_index_if_missing("ix_automation_rule_events_task_id", "automation_rule_events", ["task_id"])


def upgrade() -> None:
    _create_delegation_tables()
    _copy_if_source_exists("automation_rules", "delegation_rules", RULE_COLUMNS)
    _copy_if_source_exists("automation_rule_runs", "delegation_rule_runs", RUN_COLUMNS)
    _copy_if_source_exists("automation_rule_events", "delegation_rule_events", EVENT_COLUMNS)
    _drop_table_if_exists("automation_rule_events")
    _drop_table_if_exists("automation_rule_runs")
    _drop_table_if_exists("automation_rules")


def downgrade() -> None:
    _create_automation_tables()
    _copy_if_source_exists("delegation_rules", "automation_rules", RULE_COLUMNS)
    _copy_if_source_exists("delegation_rule_runs", "automation_rule_runs", RUN_COLUMNS)
    _copy_if_source_exists("delegation_rule_events", "automation_rule_events", EVENT_COLUMNS)
    _drop_table_if_exists("delegation_rule_events")
    _drop_table_if_exists("delegation_rule_runs")
    _drop_table_if_exists("delegation_rules")
