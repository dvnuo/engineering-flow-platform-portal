"""add automation rules tables

Revision ID: 20260420_0013
Revises: 20260416_0012
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa


revision = "20260420_0013"
down_revision = "20260416_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.create_index("ix_automation_rules_enabled_next_run_at", "automation_rules", ["enabled", "next_run_at"])
    op.create_index("ix_automation_rules_source_trigger_enabled", "automation_rules", ["source_type", "trigger_type", "enabled"])
    op.create_index("ix_automation_rules_target_agent_enabled", "automation_rules", ["target_agent_id", "enabled"])

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
    op.create_index("ix_automation_rule_runs_rule_id", "automation_rule_runs", ["rule_id"])

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
        sa.ForeignKeyConstraint(["rule_id"], ["automation_rules.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "dedupe_key", name="uq_automation_rule_events_rule_dedupe"),
    )
    op.create_index("ix_automation_rule_events_rule_id", "automation_rule_events", ["rule_id"])
    op.create_index("ix_automation_rule_events_task_id", "automation_rule_events", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_automation_rule_events_task_id", table_name="automation_rule_events")
    op.drop_index("ix_automation_rule_events_rule_id", table_name="automation_rule_events")
    op.drop_table("automation_rule_events")

    op.drop_index("ix_automation_rule_runs_rule_id", table_name="automation_rule_runs")
    op.drop_table("automation_rule_runs")

    op.drop_index("ix_automation_rules_target_agent_enabled", table_name="automation_rules")
    op.drop_index("ix_automation_rules_source_trigger_enabled", table_name="automation_rules")
    op.drop_index("ix_automation_rules_enabled_next_run_at", table_name="automation_rules")
    op.drop_table("automation_rules")
