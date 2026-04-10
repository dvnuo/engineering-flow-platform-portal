"""add delegation run metadata fields

Revision ID: 20260407_0002
Revises: 20260407_0001
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0002"
down_revision = "20260407_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_delegations", sa.Column("coordination_run_id", sa.String(length=255), nullable=True))
    op.add_column("agent_delegations", sa.Column("round_index", sa.Integer(), nullable=True))
    op.create_index("ix_agent_delegations_coordination_run_id", "agent_delegations", ["coordination_run_id"])

    op.execute("UPDATE agent_delegations SET round_index = 1 WHERE round_index IS NULL")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("agent_delegations") as batch_op:
            batch_op.alter_column("round_index", existing_type=sa.Integer(), nullable=False)
    else:
        op.alter_column("agent_delegations", "round_index", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.drop_index("ix_agent_delegations_coordination_run_id", table_name="agent_delegations")
    op.drop_column("agent_delegations", "round_index")
    op.drop_column("agent_delegations", "coordination_run_id")
