"""add task agent metadata columns on agents

Revision ID: 20260407_0004
Revises: 20260407_0003
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0004"
down_revision = "20260407_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("template_agent_id", sa.String(length=36), nullable=True))
    op.add_column("agents", sa.Column("task_scope_label", sa.String(length=255), nullable=True))
    op.add_column("agents", sa.Column("task_cleanup_policy", sa.String(length=32), nullable=True))
    op.create_index("ix_agents_template_agent_id", "agents", ["template_agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agents_template_agent_id", table_name="agents")
    op.drop_column("agents", "task_cleanup_policy")
    op.drop_column("agents", "task_scope_label")
    op.drop_column("agents", "template_agent_id")
