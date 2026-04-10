"""add agent session metadata table

Revision ID: 20260407_0005
Revises: 20260407_0004
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0005"
down_revision = "20260407_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_session_metadata",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=True),
        sa.Column("current_task_id", sa.String(length=36), nullable=True),
        sa.Column("current_delegation_id", sa.String(length=36), nullable=True),
        sa.Column("current_coordination_run_id", sa.String(length=255), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("last_execution_id", sa.String(length=255), nullable=True),
        sa.Column("latest_event_type", sa.String(length=128), nullable=True),
        sa.Column("latest_event_state", sa.String(length=64), nullable=True),
        sa.Column("snapshot_version", sa.String(length=64), nullable=True),
        sa.Column("pending_delegations_json", sa.Text(), nullable=True),
        sa.Column("runtime_events_json", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_session_metadata_session_id", "agent_session_metadata", ["session_id"], unique=True)
    op.create_index("ix_agent_session_metadata_agent_id", "agent_session_metadata", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_session_metadata_agent_id", table_name="agent_session_metadata")
    op.drop_index("ix_agent_session_metadata_session_id", table_name="agent_session_metadata")
    op.drop_table("agent_session_metadata")

