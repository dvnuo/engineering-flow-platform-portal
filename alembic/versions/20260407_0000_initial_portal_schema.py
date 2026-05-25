"""initial portal schema baseline

Revision ID: 20260407_0000
Revises:
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260407_0000"
down_revision = None
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    return any(column.get("name") == column_name for column in _inspector().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in _inspector().get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("nickname", sa.String(length=64), nullable=True),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if _has_table("users") and not _has_index("users", "ix_users_username"):
        op.create_index("ix_users_username", "users", ["username"], unique=True)

    if not _has_table("agents"):
        op.create_table(
            "agents",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column("visibility", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("image", sa.String(length=255), nullable=False),
            sa.Column("repo_url", sa.String(length=512), nullable=True),
            sa.Column("branch", sa.String(length=128), nullable=True),
            sa.Column("cpu", sa.String(length=32), nullable=True),
            sa.Column("memory", sa.String(length=32), nullable=True),
            sa.Column("disk_size_gi", sa.Integer(), nullable=False),
            sa.Column("mount_path", sa.String(length=255), nullable=False),
            sa.Column("namespace", sa.String(length=63), nullable=False),
            sa.Column("deployment_name", sa.String(length=128), nullable=False),
            sa.Column("service_name", sa.String(length=128), nullable=False),
            sa.Column("pvc_name", sa.String(length=128), nullable=False),
            sa.Column("endpoint_path", sa.String(length=255), nullable=True),
            sa.Column("agent_type", sa.String(length=32), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        if not _has_column("agents", "agent_type"):
            op.add_column(
                "agents",
                sa.Column(
                    "agent_type",
                    sa.String(length=32),
                    nullable=False,
                    server_default=sa.text("'workspace'"),
                ),
            )

    for index_name, columns in [
        ("ix_agents_owner_user_id", ["owner_user_id"]),
    ]:
        if _has_table("agents") and not _has_index("agents", index_name):
            op.create_index(index_name, "agents", columns)

    if not _has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(length=128), nullable=False),
            sa.Column("target_type", sa.String(length=32), nullable=False),
            sa.Column("target_id", sa.String(length=64), nullable=False),
            sa.Column("details_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("audit_logs")

    op.drop_index("ix_agents_owner_user_id", table_name="agents")
    op.drop_table("agents")

    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
