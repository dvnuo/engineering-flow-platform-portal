"""add delegation routing intent fields

Revision ID: 20260407_0001
Revises: 
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260407_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_delegations", sa.Column("reply_target_type", sa.String(length=32), nullable=True))
    op.add_column("agent_delegations", sa.Column("origin_session_id", sa.String(length=255), nullable=True))

    op.execute("UPDATE agent_delegations SET reply_target_type = 'leader' WHERE reply_target_type IS NULL")
    op.execute("UPDATE agent_delegations SET origin_session_id = leader_session_id WHERE origin_session_id IS NULL AND leader_session_id IS NOT NULL")

    op.alter_column("agent_delegations", "reply_target_type", existing_type=sa.String(length=32), nullable=False)


def downgrade() -> None:
    op.drop_column("agent_delegations", "origin_session_id")
    op.drop_column("agent_delegations", "reply_target_type")
