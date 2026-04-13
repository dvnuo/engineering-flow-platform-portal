"""add runtime profiles and agent runtime_profile_id

Revision ID: 20260413_0009
Revises: 20260409_0008
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260413_0009"
down_revision = "20260409_0008"
branch_labels = None
depends_on = None


ALLOWED_RUNTIME_PROFILE_SECTIONS = (
    "llm",
    "proxy",
    "jira",
    "confluence",
    "github",
    "git",
    "debug",
)


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _has_fk(table_name: str, constrained_columns: list[str], referred_table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys(table_name):
        if fk.get("referred_table") == referred_table and fk.get("constrained_columns") == constrained_columns:
            return True
    return False


def upgrade() -> None:
    if not _has_table("runtime_profiles"):
        op.create_table(
            "runtime_profiles",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_index("runtime_profiles", "ix_runtime_profiles_name"):
        op.create_index("ix_runtime_profiles_name", "runtime_profiles", ["name"], unique=True)

    if not _has_column("agents", "runtime_profile_id"):
        op.add_column("agents", sa.Column("runtime_profile_id", sa.String(length=36), nullable=True))

    if not _has_index("agents", "ix_agents_runtime_profile_id"):
        op.create_index("ix_agents_runtime_profile_id", "agents", ["runtime_profile_id"])

    if _has_column("agents", "runtime_profile_id") and not _has_fk("agents", ["runtime_profile_id"], "runtime_profiles"):
        op.create_foreign_key(
            "fk_agents_runtime_profile_id_runtime_profiles",
            "agents",
            "runtime_profiles",
            ["runtime_profile_id"],
            ["id"],
        )


def downgrade() -> None:
    if _has_table("agents"):
        if _has_fk("agents", ["runtime_profile_id"], "runtime_profiles"):
            op.drop_constraint("fk_agents_runtime_profile_id_runtime_profiles", "agents", type_="foreignkey")
        if _has_index("agents", "ix_agents_runtime_profile_id"):
            op.drop_index("ix_agents_runtime_profile_id", table_name="agents")
        if _has_column("agents", "runtime_profile_id"):
            op.drop_column("agents", "runtime_profile_id")

    if _has_table("runtime_profiles"):
        if _has_index("runtime_profiles", "ix_runtime_profiles_name"):
            op.drop_index("ix_runtime_profiles_name", table_name="runtime_profiles")
        op.drop_table("runtime_profiles")
