"""add agent coordination runs table

Revision ID: 20260407_0003
Revises: 20260407_0002
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0003"
down_revision = "20260407_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_coordination_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("leader_agent_id", sa.String(length=36), nullable=False),
        sa.Column("origin_session_id", sa.String(length=255), nullable=True),
        sa.Column("coordination_run_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latest_round_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["leader_agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_coordination_run_group", "agent_coordination_runs", ["group_id"])
    op.create_index("ix_agent_coordination_run_leader", "agent_coordination_runs", ["leader_agent_id"])
    op.create_index("ix_agent_coordination_run_coordination", "agent_coordination_runs", ["coordination_run_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_agent_coordination_run_coordination", table_name="agent_coordination_runs")
    op.drop_index("ix_agent_coordination_run_leader", table_name="agent_coordination_runs")
    op.drop_index("ix_agent_coordination_run_group", table_name="agent_coordination_runs")
    op.drop_table("agent_coordination_runs")
