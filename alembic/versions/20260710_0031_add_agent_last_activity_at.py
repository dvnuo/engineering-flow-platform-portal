"""add agent last_activity_at

Revision ID: 20260710_0031
Revises: 20260709_0030
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260710_0031"
down_revision = "20260709_0030"
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


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("agents"):
        return

    with op.batch_alter_table("agents") as batch_op:
        if not _has_column("agents", "last_activity_at"):
            batch_op.add_column(sa.Column("last_activity_at", sa.DateTime(), nullable=True))

    if not _has_index("agents", "ix_agents_last_activity_at"):
        op.create_index("ix_agents_last_activity_at", "agents", ["last_activity_at"])


def downgrade() -> None:
    if not _has_table("agents"):
        return

    if _has_index("agents", "ix_agents_last_activity_at"):
        op.drop_index("ix_agents_last_activity_at", table_name="agents")

    with op.batch_alter_table("agents") as batch_op:
        if _has_column("agents", "last_activity_at"):
            batch_op.drop_column("last_activity_at")
