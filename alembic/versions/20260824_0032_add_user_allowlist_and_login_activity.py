"""add user allowlist and login activity

Revision ID: 20260824_0032
Revises: 20260710_0031
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0032"
down_revision = "20260710_0031"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def upgrade() -> None:
    if _has_table("users") and not _has_column("users", "last_login_at"):
        op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    if not _has_table("user_allowlist"):
        op.create_table(
            "user_allowlist",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("added_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_index("user_allowlist", "ix_user_allowlist_username"):
        op.create_index(
            "ix_user_allowlist_username",
            "user_allowlist",
            ["username"],
            unique=True,
        )


def downgrade() -> None:
    if _has_table("user_allowlist"):
        if _has_index("user_allowlist", "ix_user_allowlist_username"):
            op.drop_index("ix_user_allowlist_username", table_name="user_allowlist")
        op.drop_table("user_allowlist")
    if _has_table("users") and _has_column("users", "last_login_at"):
        op.drop_column("users", "last_login_at")
