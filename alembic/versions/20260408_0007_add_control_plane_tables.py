"""add active portal tables missing from migrations

Revision ID: 20260408_0007
Revises: 20260408_0006
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260408_0007"
down_revision = "20260408_0006"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("agent_tasks"):
        op.create_table(
            "agent_tasks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("assignee_agent_id", sa.String(length=36), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("task_type", sa.String(length=128), nullable=False),
            sa.Column("input_payload_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("result_payload_json", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["assignee_agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if _has_table("agent_tasks") and not _has_index("agent_tasks", "ix_agent_tasks_assignee_agent_id"):
        op.create_index("ix_agent_tasks_assignee_agent_id", "agent_tasks", ["assignee_agent_id"])

    if not _has_table("runtime_capability_catalog_snapshots"):
        op.create_table(
            "runtime_capability_catalog_snapshots",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_agent_id", sa.String(length=36), nullable=True),
            sa.Column("catalog_version", sa.String(length=128), nullable=True),
            sa.Column("catalog_source", sa.String(length=64), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    for index_name, columns in [
        ("ix_runtime_capability_catalog_snapshots_source_agent_id", ["source_agent_id"]),
        ("ix_runtime_capability_catalog_snapshots_fetched_at", ["fetched_at"]),
    ]:
        if _has_table("runtime_capability_catalog_snapshots") and not _has_index("runtime_capability_catalog_snapshots", index_name):
            op.create_index(index_name, "runtime_capability_catalog_snapshots", columns)


def downgrade() -> None:
    if _has_table("runtime_capability_catalog_snapshots"):
        for index_name in [
            "ix_runtime_capability_catalog_snapshots_fetched_at",
            "ix_runtime_capability_catalog_snapshots_source_agent_id",
        ]:
            if _has_index("runtime_capability_catalog_snapshots", index_name):
                op.drop_index(index_name, table_name="runtime_capability_catalog_snapshots")
        op.drop_table("runtime_capability_catalog_snapshots")

    if _has_table("agent_tasks"):
        if _has_index("agent_tasks", "ix_agent_tasks_assignee_agent_id"):
            op.drop_index("ix_agent_tasks_assignee_agent_id", table_name="agent_tasks")
        op.drop_table("agent_tasks")
