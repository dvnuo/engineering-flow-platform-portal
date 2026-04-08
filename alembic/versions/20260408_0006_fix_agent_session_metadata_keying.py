"""fix agent session metadata keying to agent-scoped session uniqueness

Revision ID: 20260408_0006
Revises: 20260407_0005
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260408_0006"
down_revision = "20260407_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_agent_session_metadata_session_id", table_name="agent_session_metadata")
    op.create_unique_constraint(
        "uq_agent_session_metadata_agent_session",
        "agent_session_metadata",
        ["agent_id", "session_id"],
    )
    op.create_index(
        "ix_agent_session_metadata_agent_updated_at",
        "agent_session_metadata",
        ["agent_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_session_metadata_agent_updated_at", table_name="agent_session_metadata")
    op.drop_constraint(
        "uq_agent_session_metadata_agent_session",
        "agent_session_metadata",
        type_="unique",
    )
    op.create_index(
        "ix_agent_session_metadata_session_id",
        "agent_session_metadata",
        ["session_id"],
        unique=True,
    )

