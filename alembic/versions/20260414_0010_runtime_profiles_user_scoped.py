"""runtime profiles user-scoped ownership and defaults

Revision ID: 20260414_0010
Revises: 20260413_0009
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa


revision = "20260414_0010"
down_revision = "20260413_0009"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("runtime_profiles", "owner_user_id"):
        op.add_column("runtime_profiles", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    if not _has_column("runtime_profiles", "is_default"):
        op.add_column("runtime_profiles", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))

    if not _has_index("runtime_profiles", "ix_runtime_profiles_owner_user_id"):
        op.create_index("ix_runtime_profiles_owner_user_id", "runtime_profiles", ["owner_user_id"], unique=False)

    if _has_index("runtime_profiles", "ix_runtime_profiles_name"):
        op.drop_index("ix_runtime_profiles_name", table_name="runtime_profiles")

    if not _has_index("runtime_profiles", "uq_runtime_profiles_owner_name"):
        op.create_index(
            "uq_runtime_profiles_owner_name",
            "runtime_profiles",
            ["owner_user_id", "name"],
            unique=True,
        )


def downgrade() -> None:
    if _has_index("runtime_profiles", "uq_runtime_profiles_owner_name"):
        op.drop_index("uq_runtime_profiles_owner_name", table_name="runtime_profiles")

    if not _has_index("runtime_profiles", "ix_runtime_profiles_name"):
        op.create_index("ix_runtime_profiles_name", "runtime_profiles", ["name"], unique=True)

    if _has_index("runtime_profiles", "ix_runtime_profiles_owner_user_id"):
        op.drop_index("ix_runtime_profiles_owner_user_id", table_name="runtime_profiles")

    if _has_column("runtime_profiles", "is_default"):
        op.drop_column("runtime_profiles", "is_default")
    if _has_column("runtime_profiles", "owner_user_id"):
        op.drop_column("runtime_profiles", "owner_user_id")
