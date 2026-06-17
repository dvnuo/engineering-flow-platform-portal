"""add agent settings repo fields

Revision ID: 20260617_0029
Revises: 20260617_0028
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260617_0029"
down_revision = "20260617_0028"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def upgrade() -> None:
    if not _has_table("agents"):
        return

    with op.batch_alter_table("agents") as batch_op:
        if not _has_column("agents", "agent_settings_repo_url"):
            batch_op.add_column(sa.Column("agent_settings_repo_url", sa.String(length=512), nullable=True))
        if not _has_column("agents", "agent_settings_branch"):
            batch_op.add_column(sa.Column("agent_settings_branch", sa.String(length=128), nullable=True))
        if not _has_column("agents", "agent_settings_subdir"):
            batch_op.add_column(sa.Column("agent_settings_subdir", sa.String(length=255), nullable=True))


def downgrade() -> None:
    if not _has_table("agents"):
        return

    with op.batch_alter_table("agents") as batch_op:
        if _has_column("agents", "agent_settings_subdir"):
            batch_op.drop_column("agent_settings_subdir")
        if _has_column("agents", "agent_settings_branch"):
            batch_op.drop_column("agent_settings_branch")
        if _has_column("agents", "agent_settings_repo_url"):
            batch_op.drop_column("agent_settings_repo_url")
