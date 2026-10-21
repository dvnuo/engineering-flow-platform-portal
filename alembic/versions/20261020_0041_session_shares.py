"""session_shares: read-only, live shares of one chat session

Revision ID: 20261020_0041
Revises: 20261020_0040
Create Date: 2026-10-20

A member can share one session read-only at ``/share/{token}``. The page
reads the session live from the runtime, so the row holds only the access
rule (``scope``: ``members`` for signed-in allowlisted members, ``anyone``
for whoever has the link), the owner, the name at share time (shown while
the pod is stopped) and view counters. One share per session; revoking
deletes the row.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261020_0041"
down_revision = "20261020_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_shares",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="members"),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "session_id", name="uq_session_shares_agent_session"),
    )
    op.create_index("ix_session_shares_token", "session_shares", ["token"], unique=True)
    op.create_index("ix_session_shares_agent_id", "session_shares", ["agent_id"], unique=False)
    op.create_index("ix_session_shares_owner_user_id", "session_shares", ["owner_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_session_shares_owner_user_id", table_name="session_shares")
    op.drop_index("ix_session_shares_agent_id", table_name="session_shares")
    op.drop_index("ix_session_shares_token", table_name="session_shares")
    op.drop_table("session_shares")
