"""add agent execution shadow registry

Revision ID: 20260614_0027
Revises: 20260525_0026
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260614_0027"
down_revision = "20260525_0026"
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


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str]) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    if not _has_table("agent_executions"):
        op.create_table(
            "agent_executions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=128), nullable=True),
            sa.Column("request_id", sa.String(length=128), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=True),
            sa.Column("runtime_type", sa.String(length=32), nullable=True),
            sa.Column("runtime_task_id", sa.String(length=128), nullable=True),
            sa.Column("task_id", sa.String(length=36), nullable=True),
            sa.Column("execution_path", sa.String(length=128), nullable=True),
            sa.Column("owner_user_id", sa.Integer(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("runtime_status_code", sa.Integer(), nullable=True),
            sa.Column("would_conflict_same_session", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("error_code", sa.String(length=128), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("last_event_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    _create_index_if_missing("ix_agent_executions_agent_id", "agent_executions", ["agent_id"])
    _create_index_if_missing("ix_agent_executions_session_id", "agent_executions", ["session_id"])
    _create_index_if_missing("ix_agent_executions_request_id", "agent_executions", ["request_id"])
    _create_index_if_missing("ix_agent_executions_kind", "agent_executions", ["kind"])
    _create_index_if_missing("ix_agent_executions_status", "agent_executions", ["status"])
    _create_index_if_missing("ix_agent_executions_runtime_task_id", "agent_executions", ["runtime_task_id"])
    _create_index_if_missing("ix_agent_executions_task_id", "agent_executions", ["task_id"])
    _create_index_if_missing("ix_agent_executions_owner_user_id", "agent_executions", ["owner_user_id"])
    _create_index_if_missing("ix_agent_executions_created_by_user_id", "agent_executions", ["created_by_user_id"])
    _create_index_if_missing("ix_agent_executions_agent_session_status", "agent_executions", ["agent_id", "session_id", "status"])


def downgrade() -> None:
    if _has_table("agent_executions"):
        op.drop_table("agent_executions")
