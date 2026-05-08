"""add runtime profile sync jobs table

Revision ID: 20260508_0019
Revises: 20260506_0018
Create Date: 2026-05-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260508_0019"
down_revision = "20260506_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_profile_sync_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("runtime_profile_id", sa.String(length=36), nullable=True),
        sa.Column("requested_revision", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_profile_sync_jobs_agent_id", "runtime_profile_sync_jobs", ["agent_id"], unique=False)
    op.create_index("ix_runtime_profile_sync_jobs_runtime_profile_id", "runtime_profile_sync_jobs", ["runtime_profile_id"], unique=False)
    op.create_index("ix_runtime_profile_sync_jobs_due", "runtime_profile_sync_jobs", ["status", "next_run_at"], unique=False)
    op.create_index("ix_runtime_profile_sync_jobs_agent_status", "runtime_profile_sync_jobs", ["agent_id", "status"], unique=False)
    op.create_index("ix_runtime_profile_sync_jobs_lock", "runtime_profile_sync_jobs", ["locked_until"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_runtime_profile_sync_jobs_lock", table_name="runtime_profile_sync_jobs")
    op.drop_index("ix_runtime_profile_sync_jobs_agent_status", table_name="runtime_profile_sync_jobs")
    op.drop_index("ix_runtime_profile_sync_jobs_due", table_name="runtime_profile_sync_jobs")
    op.drop_index("ix_runtime_profile_sync_jobs_runtime_profile_id", table_name="runtime_profile_sync_jobs")
    op.drop_index("ix_runtime_profile_sync_jobs_agent_id", table_name="runtime_profile_sync_jobs")
    op.drop_table("runtime_profile_sync_jobs")
