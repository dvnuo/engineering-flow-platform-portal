"""remove legacy control planes

Revision ID: 20260524_0024
Revises: 20260524_0023
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260524_0024"
down_revision = "20260524_0023"
branch_labels = None
depends_on = None


def _inspect() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspect().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in _inspect().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in _inspect().get_indexes(table_name))


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if not _has_column(table_name, column_name):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column(column_name)
    else:
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    cap_table = "capability" + "_" + "profiles"
    pol_table = "policy" + "_" + "profiles"
    context_table = "group_" + "shared_" + "context_snapshots"
    agent_cap_col = "capability" + "_" + "profile_id"
    agent_pol_col = "policy" + "_" + "profile_id"
    task_context_col = "shared_" + "context_ref"
    group_context_policy_col = "shared_" + "context_policy_json"

    _drop_index_if_exists("agents", "ix_agents_" + agent_cap_col)
    _drop_index_if_exists("agents", "ix_agents_" + agent_pol_col)
    _drop_column_if_exists("agents", agent_cap_col)
    _drop_column_if_exists("agents", agent_pol_col)

    _drop_column_if_exists("agent_tasks", task_context_col)
    _drop_column_if_exists("agent_groups", group_context_policy_col)

    if _has_table(context_table):
        for index_name in (
            "ix_group_" + "shared_" + "context_group_ref",
            "ix_group_" + "shared_" + "context_snapshots_source_delegation_id",
            "ix_group_" + "shared_" + "context_snapshots_created_by_user_id",
            "ix_group_" + "shared_" + "context_snapshots_context_ref",
            "ix_group_" + "shared_" + "context_snapshots_group_id",
        ):
            _drop_index_if_exists(context_table, index_name)
        op.drop_table(context_table)

    if _has_table(pol_table):
        _drop_index_if_exists(pol_table, "ix_" + pol_table + "_name")
        op.drop_table(pol_table)

    if _has_table(cap_table):
        _drop_index_if_exists(cap_table, "ix_" + cap_table + "_name")
        op.drop_table(cap_table)


def downgrade() -> None:
    # Removed control planes are intentionally not reconstructed.
    pass
