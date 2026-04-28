"""add task template ids for tasks and automation

Revision ID: 20260428_0015
Revises: 20260420_0014_add_automation_rule_event_updated_at
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260428_0015"
down_revision = "20260420_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_tasks", sa.Column("template_id", sa.String(length=128), nullable=True))
    op.create_index("ix_agent_tasks_template_id", "agent_tasks", ["template_id"])

    op.add_column("automation_rules", sa.Column("task_template_id", sa.String(length=128), nullable=True))
    op.execute("UPDATE automation_rules SET task_template_id = 'github_pr_review' WHERE task_template_id IS NULL")


def downgrade() -> None:
    op.drop_column("automation_rules", "task_template_id")
    op.drop_index("ix_agent_tasks_template_id", table_name="agent_tasks")
    op.drop_column("agent_tasks", "template_id")
