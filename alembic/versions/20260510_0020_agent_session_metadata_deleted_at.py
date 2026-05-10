"""add deleted_at to agent_session_metadata

Revision ID: 20260510_0020
Revises: 20260508_0019
Create Date: 2026-05-10
"""

from alembic import op
import sqlalchemy as sa

revision = "20260510_0020"
down_revision = "20260508_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_session_metadata") as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_agent_session_metadata_agent_deleted_updated_at",
        "agent_session_metadata",
        ["agent_id", "deleted_at", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_session_metadata_agent_deleted_updated_at", table_name="agent_session_metadata")
    with op.batch_alter_table("agent_session_metadata") as batch_op:
        batch_op.drop_column("deleted_at")
