"""collapse viewer role into user

The "viewer" role was assignable but never enforced: every permission check in
Portal is `role == "admin" or agent.owner_user_id == user.id`, so a viewer could
create and modify assistants exactly like a user. Keeping it in the dropdown
promised a read-only account that did not exist, so the role is gone and any
rows still carrying it are folded into "user".

Revision ID: 20260825_0033
Revises: 20260824_0032
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_0033"
down_revision = "20260824_0032"
branch_labels = None
depends_on = None

_ROLE_TABLES = ("users", "user_allowlist")


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_role_column(table_name: str) -> bool:
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == "role" for column in inspector.get_columns(table_name))


def upgrade() -> None:
    for table_name in _ROLE_TABLES:
        if not _has_role_column(table_name):
            continue
        op.execute(
            sa.text(f"UPDATE {table_name} SET role = 'user' WHERE role = 'viewer'")  # noqa: S608
        )


def downgrade() -> None:
    # Rows that were viewers before the upgrade are indistinguishable from real
    # users afterwards, and restoring the role would hand them back an
    # unenforced permission level. Downgrade is intentionally a no-op.
    pass
