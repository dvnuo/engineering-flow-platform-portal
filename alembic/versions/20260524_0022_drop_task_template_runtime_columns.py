"""drop task template runtime columns

Revision ID: 20260524_0022
Revises: 20260524_0021
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260524_0022"
down_revision = "20260524_0021"
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
    dialect = op.get_bind().dialect.name

    if _has_index("agent_tasks", "ix_agent_tasks_template_id"):
        op.drop_index("ix_agent_tasks_template_id", table_name="agent_tasks")

    if dialect == "sqlite":
        agent_task_columns = [
            column_name
            for column_name in ["template_id"]
            if _has_column("agent_tasks", column_name)
        ]
        automation_rule_columns = [
            column_name
            for column_name in ["task_template_id"]
            if _has_column("automation_rules", column_name)
        ]
        if agent_task_columns:
            with op.batch_alter_table("agent_tasks") as batch_op:
                for column_name in agent_task_columns:
                    batch_op.drop_column(column_name)
        if automation_rule_columns:
            with op.batch_alter_table("automation_rules") as batch_op:
                for column_name in automation_rule_columns:
                    batch_op.drop_column(column_name)
    else:
        if _has_column("automation_rules", "task_template_id"):
            op.drop_column("automation_rules", "task_template_id")
        if _has_column("agent_tasks", "template_id"):
            op.drop_column("agent_tasks", "template_id")


def downgrade() -> None:
    # Removed compatibility columns are intentionally not recreated.
    pass
