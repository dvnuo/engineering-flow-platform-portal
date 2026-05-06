"""drop runtime_type server default from agents

Revision ID: 20260506_0018
Revises: 20260502_0017
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260506_0018"
down_revision = "20260502_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column(
            "runtime_type",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column(
            "runtime_type",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default="native",
        )
