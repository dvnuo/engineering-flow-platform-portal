"""drop external_event_subscriptions table

Revision ID: 20260416_0012
Revises: 20260414_0011
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260416_0012"
down_revision = "20260414_0011"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("external_event_subscriptions"):
        return

    for index_name in (
        "ix_external_sub_source_kind_enabled",
        "ix_external_sub_agent_enabled_mode",
        "ix_external_event_subscriptions_agent_id",
    ):
        if _has_index("external_event_subscriptions", index_name):
            op.drop_index(index_name, table_name="external_event_subscriptions")

    op.drop_table("external_event_subscriptions")


def downgrade() -> None:
    op.create_table(
        "external_event_subscriptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("target_ref", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("dedupe_key_template", sa.String(length=255), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="push"),
        sa.Column("source_kind", sa.String(length=128), nullable=True),
        sa.Column("binding_id", sa.String(length=36), nullable=True),
        sa.Column("scope_json", sa.Text(), nullable=True),
        sa.Column("matcher_json", sa.Text(), nullable=True),
        sa.Column("routing_json", sa.Text(), nullable=True),
        sa.Column("poll_profile_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_event_subscriptions_agent_id", "external_event_subscriptions", ["agent_id"])
    op.create_index(
        "ix_external_sub_agent_enabled_mode",
        "external_event_subscriptions",
        ["agent_id", "enabled", "mode"],
    )
    op.create_index(
        "ix_external_sub_source_kind_enabled",
        "external_event_subscriptions",
        ["source_kind", "enabled"],
    )
