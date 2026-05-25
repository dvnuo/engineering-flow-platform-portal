"""add agent runtime type field

Revision ID: 20260502_0017
Revises: 20260430_0016
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260502_0017"
down_revision = "20260430_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("runtime_type", sa.String(length=32), nullable=False, server_default="native"),
    )
    op.create_index("ix_agents_runtime_type", "agents", ["runtime_type"])


def downgrade() -> None:
    op.drop_index("ix_agents_runtime_type", table_name="agents")
    op.drop_column("agents", "runtime_type")
