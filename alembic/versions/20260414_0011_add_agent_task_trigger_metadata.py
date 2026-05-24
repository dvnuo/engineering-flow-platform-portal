"""add agent task trigger metadata

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


def upgrade() -> None:
    if not _has_column("agent_tasks", "task_family"):
        op.add_column("agent_tasks", sa.Column("task_family", sa.String(length=64), nullable=True))
    if not _has_column("agent_tasks", "provider"):
        op.add_column("agent_tasks", sa.Column("provider", sa.String(length=64), nullable=True))
    if not _has_column("agent_tasks", "trigger"):
        op.add_column("agent_tasks", sa.Column("trigger", sa.String(length=128), nullable=True))
    if not _has_column("agent_tasks", "version_key"):
        op.add_column("agent_tasks", sa.Column("version_key", sa.String(length=255), nullable=True))
    if not _has_column("agent_tasks", "dedupe_key"):
        op.add_column("agent_tasks", sa.Column("dedupe_key", sa.String(length=255), nullable=True))

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


def downgrade() -> None:
    if _has_column("agent_tasks", "dedupe_key"):
        op.drop_column("agent_tasks", "dedupe_key")
    if _has_column("agent_tasks", "version_key"):
        op.drop_column("agent_tasks", "version_key")
    if _has_column("agent_tasks", "trigger"):
        op.drop_column("agent_tasks", "trigger")
    if _has_column("agent_tasks", "provider"):
        op.drop_column("agent_tasks", "provider")
    if _has_column("agent_tasks", "task_family"):
        op.drop_column("agent_tasks", "task_family")
