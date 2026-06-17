"""add agent task list indexes

Revision ID: 20260617_0028
Revises: 20260614_0027
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260617_0028"
down_revision = "20260614_0027"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in _inspector().get_indexes(table_name))


def _create_index_if_missing(index_name: str, columns: list[str]) -> None:
    if not _has_index("agent_tasks", index_name):
        op.create_index(index_name, "agent_tasks", columns)


def _drop_index_if_exists(index_name: str) -> None:
    if _has_index("agent_tasks", index_name):
        op.drop_index(index_name, table_name="agent_tasks")


def upgrade() -> None:
    if not _has_table("agent_tasks"):
        return
    _create_index_if_missing("ix_agent_tasks_updated_created_id", ["updated_at", "created_at", "id"])
    _create_index_if_missing("ix_agent_tasks_status_updated_created_id", ["status", "updated_at", "created_at", "id"])
    _create_index_if_missing("ix_agent_tasks_owner_updated_created_id", ["owner_user_id", "updated_at", "created_at", "id"])
    _create_index_if_missing(
        "ix_agent_tasks_owner_status_updated_created_id",
        ["owner_user_id", "status", "updated_at", "created_at", "id"],
    )


def downgrade() -> None:
    for index_name in [
        "ix_agent_tasks_owner_status_updated_created_id",
        "ix_agent_tasks_owner_updated_created_id",
        "ix_agent_tasks_status_updated_created_id",
        "ix_agent_tasks_updated_created_id",
    ]:
        _drop_index_if_exists(index_name)
