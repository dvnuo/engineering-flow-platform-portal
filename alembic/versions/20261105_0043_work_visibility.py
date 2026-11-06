"""visibility of tasks and delegation rules: private by default, public on request

Revision ID: 20261105_0043
Revises: 20261028_0042
Create Date: 2026-11-05

Every task and every delegation rule used to be listed for every member.
Both tables now carry ``visibility``, ``private`` (the owner and admins) or
``public`` (every member), the two states assistants already have. Existing
rows become private, which is the default the members asked for; the owner
switches a row to public from its detail or edit form
(app/services/work_visibility.py). The columns are guarded like
``users.preferences_json`` so a development database that already has them
is left alone.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261105_0043"
down_revision = "20261028_0042"
branch_labels = None
depends_on = None

TABLES = ("agent_tasks", "delegation_rules")


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    for table in TABLES:
        if not _has_column(table, "visibility"):
            op.add_column(table, sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"))
            op.create_index(f"ix_{table}_visibility", table, ["visibility"], unique=False)


def downgrade() -> None:
    for table in TABLES:
        if _has_column(table, "visibility"):
            op.drop_index(f"ix_{table}_visibility", table_name=table)
            op.drop_column(table, "visibility")
