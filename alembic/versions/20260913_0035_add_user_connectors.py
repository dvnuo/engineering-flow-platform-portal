"""add user connectors

Revision ID: 20260913_0035
Revises: 20260828_0034
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260913_0035"
down_revision = "20260828_0034"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def upgrade() -> None:
    # Per-member connector settings (docs/CONNECTORS_CONTRACT.md §7). One row per
    # (member, connector type); the proxy reads enabled rows on every chat
    # request, so the table stays narrow and the config stays JSON.
    if not _has_table("user_connectors"):
        op.create_table(
            "user_connectors",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column("connector_type", sa.String(length=64), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("last_verified_at", sa.DateTime(), nullable=True),
            sa.Column("verification_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_user_id", "connector_type", name="uq_user_connectors_owner_type"),
        )
    if not _has_index("user_connectors", "ix_user_connectors_owner_user_id"):
        op.create_index(
            "ix_user_connectors_owner_user_id",
            "user_connectors",
            ["owner_user_id"],
            unique=False,
        )


def downgrade() -> None:
    if _has_table("user_connectors"):
        if _has_index("user_connectors", "ix_user_connectors_owner_user_id"):
            op.drop_index("ix_user_connectors_owner_user_id", table_name="user_connectors")
        op.drop_table("user_connectors")
