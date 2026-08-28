"""add assistant types, platform settings, and onboarding tracking

Revision ID: 20260828_0034
Revises: 20260825_0033
Create Date: 2026-08-28
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "20260828_0034"
down_revision = "20260825_0033"
branch_labels = None
depends_on = None


DEFAULT_ASSISTANT_TYPES = (
    (
        "business",
        "Business Assistant",
        "Requirements, tickets, and documentation. Asks before it acts.",
        "clipboard-list",
        10,
    ),
    (
        "dev",
        "Dev Assistant",
        "Code, pull requests, and reviews across your repositories.",
        "code",
        20,
    ),
    (
        "ops",
        "Ops Assistant",
        "Deployments, monitoring, and runbooks for live systems.",
        "server-cog",
        30,
    ),
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def upgrade() -> None:
    if not _has_table("assistant_types"):
        op.create_table(
            "assistant_types",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("icon", sa.String(length=64), nullable=False, server_default="bot"),
            sa.Column("runtime_type", sa.String(length=32), nullable=False, server_default="native"),
            sa.Column("agent_settings_branch", sa.String(length=128), nullable=True),
            sa.Column("skill_branch", sa.String(length=128), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

        # Seed the three roles the platform is being opened to. Branches stay
        # NULL so a fresh install falls back to the configured defaults and the
        # simple create flow works before an admin has customized anything.
        now = datetime.utcnow()
        op.bulk_insert(
            sa.table(
                "assistant_types",
                sa.column("id", sa.String),
                sa.column("name", sa.String),
                sa.column("description", sa.Text),
                sa.column("icon", sa.String),
                sa.column("runtime_type", sa.String),
                sa.column("agent_settings_branch", sa.String),
                sa.column("skill_branch", sa.String),
                sa.column("sort_order", sa.Integer),
                sa.column("is_active", sa.Boolean),
                sa.column("created_at", sa.DateTime),
                sa.column("updated_at", sa.DateTime),
            ),
            [
                {
                    "id": type_id,
                    "name": name,
                    "description": description,
                    "icon": icon,
                    "runtime_type": "native",
                    "agent_settings_branch": None,
                    "skill_branch": None,
                    "sort_order": sort_order,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
                for type_id, name, description, icon, sort_order in DEFAULT_ASSISTANT_TYPES
            ],
        )

    if not _has_table("platform_settings"):
        op.create_table(
            "platform_settings",
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("value_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("key"),
        )

    if _has_table("users") and not _has_column("users", "onboarding_completed_at"):
        op.add_column("users", sa.Column("onboarding_completed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "onboarding_completed_at"):
        op.drop_column("users", "onboarding_completed_at")
    if _has_table("platform_settings"):
        op.drop_table("platform_settings")
    if _has_table("assistant_types"):
        op.drop_table("assistant_types")
