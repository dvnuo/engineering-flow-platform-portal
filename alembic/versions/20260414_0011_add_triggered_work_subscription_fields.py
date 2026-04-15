"""add triggered-work task metadata and subscription fields

Revision ID: 20260414_0011
Revises: 20260414_0010
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260414_0011"
down_revision = "20260414_0010"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("external_event_subscriptions", "mode"):
        op.add_column("external_event_subscriptions", sa.Column("mode", sa.String(length=16), nullable=True))
    if not _has_column("external_event_subscriptions", "source_kind"):
        op.add_column("external_event_subscriptions", sa.Column("source_kind", sa.String(length=128), nullable=True))
    if not _has_column("external_event_subscriptions", "binding_id"):
        op.add_column("external_event_subscriptions", sa.Column("binding_id", sa.String(length=36), nullable=True))
    if not _has_column("external_event_subscriptions", "scope_json"):
        op.add_column("external_event_subscriptions", sa.Column("scope_json", sa.Text(), nullable=True))
    if not _has_column("external_event_subscriptions", "matcher_json"):
        op.add_column("external_event_subscriptions", sa.Column("matcher_json", sa.Text(), nullable=True))
    if not _has_column("external_event_subscriptions", "routing_json"):
        op.add_column("external_event_subscriptions", sa.Column("routing_json", sa.Text(), nullable=True))
    if not _has_column("external_event_subscriptions", "poll_profile_json"):
        op.add_column("external_event_subscriptions", sa.Column("poll_profile_json", sa.Text(), nullable=True))

    if not _has_column("agent_tasks", "task_family"):
        op.add_column("agent_tasks", sa.Column("task_family", sa.String(length=64), nullable=True))
    if not _has_column("agent_tasks", "provider"):
        op.add_column("agent_tasks", sa.Column("provider", sa.String(length=64), nullable=True))
    if not _has_column("agent_tasks", "trigger"):
        op.add_column("agent_tasks", sa.Column("trigger", sa.String(length=128), nullable=True))
    if not _has_column("agent_tasks", "bundle_id"):
        op.add_column("agent_tasks", sa.Column("bundle_id", sa.String(length=255), nullable=True))
    if not _has_column("agent_tasks", "version_key"):
        op.add_column("agent_tasks", sa.Column("version_key", sa.String(length=255), nullable=True))
    if not _has_column("agent_tasks", "dedupe_key"):
        op.add_column("agent_tasks", sa.Column("dedupe_key", sa.String(length=255), nullable=True))

    if not _has_index("external_event_subscriptions", "ix_external_sub_agent_enabled_mode"):
        op.create_index(
            "ix_external_sub_agent_enabled_mode",
            "external_event_subscriptions",
            ["agent_id", "enabled", "mode"],
            unique=False,
        )
    if not _has_index("external_event_subscriptions", "ix_external_sub_source_kind_enabled"):
        op.create_index(
            "ix_external_sub_source_kind_enabled",
            "external_event_subscriptions",
            ["source_kind", "enabled"],
            unique=False,
        )

    op.execute("UPDATE external_event_subscriptions SET mode = 'push' WHERE mode IS NULL")
    op.execute(
        "UPDATE external_event_subscriptions "
        "SET source_kind = lower(coalesce(source_type, '')) || '.' || coalesce(event_type, '') "
        "WHERE source_kind IS NULL"
    )

    op.execute(
        "UPDATE agent_tasks "
        "SET task_family = 'triggered_work' "
        "WHERE task_family IS NULL AND lower(coalesce(source, '')) IN ('github','jira','confluence')"
    )
    op.execute("UPDATE agent_tasks SET provider = source WHERE provider IS NULL")
    op.execute(
        "UPDATE agent_tasks "
        "SET trigger = 'pull_request_review_requested' "
        "WHERE trigger IS NULL AND task_type = 'github_review_task'"
    )
    op.execute(
        "UPDATE agent_tasks "
        "SET trigger = 'workflow_review_requested' "
        "WHERE trigger IS NULL AND task_type = 'jira_workflow_review_task'"
    )
    op.execute(
        "UPDATE agent_tasks "
        "SET dedupe_key = shared_context_ref "
        "WHERE dedupe_key IS NULL AND shared_context_ref IS NOT NULL"
    )


def downgrade() -> None:
    if _has_index("external_event_subscriptions", "ix_external_sub_source_kind_enabled"):
        op.drop_index("ix_external_sub_source_kind_enabled", table_name="external_event_subscriptions")
    if _has_index("external_event_subscriptions", "ix_external_sub_agent_enabled_mode"):
        op.drop_index("ix_external_sub_agent_enabled_mode", table_name="external_event_subscriptions")

    if _has_column("agent_tasks", "dedupe_key"):
        op.drop_column("agent_tasks", "dedupe_key")
    if _has_column("agent_tasks", "version_key"):
        op.drop_column("agent_tasks", "version_key")
    if _has_column("agent_tasks", "bundle_id"):
        op.drop_column("agent_tasks", "bundle_id")
    if _has_column("agent_tasks", "trigger"):
        op.drop_column("agent_tasks", "trigger")
    if _has_column("agent_tasks", "provider"):
        op.drop_column("agent_tasks", "provider")
    if _has_column("agent_tasks", "task_family"):
        op.drop_column("agent_tasks", "task_family")

    if _has_column("external_event_subscriptions", "poll_profile_json"):
        op.drop_column("external_event_subscriptions", "poll_profile_json")
    if _has_column("external_event_subscriptions", "routing_json"):
        op.drop_column("external_event_subscriptions", "routing_json")
    if _has_column("external_event_subscriptions", "matcher_json"):
        op.drop_column("external_event_subscriptions", "matcher_json")
    if _has_column("external_event_subscriptions", "scope_json"):
        op.drop_column("external_event_subscriptions", "scope_json")
    if _has_column("external_event_subscriptions", "binding_id"):
        op.drop_column("external_event_subscriptions", "binding_id")
    if _has_column("external_event_subscriptions", "source_kind"):
        op.drop_column("external_event_subscriptions", "source_kind")
    if _has_column("external_event_subscriptions", "mode"):
        op.drop_column("external_event_subscriptions", "mode")
