"""drop runtime_profile_sync_jobs

Runtime profile distribution moved from the HTTP push queue to per-profile
Kubernetes Secrets injected as pod env; the durable sync-job queue is gone.

Revision ID: 20260709_0030
Revises: 20260617_0029
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260709_0030"
down_revision = "20260617_0029"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("runtime_profile_sync_jobs"):
        op.drop_table("runtime_profile_sync_jobs")


def downgrade() -> None:
    if _has_table("runtime_profile_sync_jobs"):
        return
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
