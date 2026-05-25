"""drop obsolete agent task bundle field

Revision ID: 20260524_0023
Revises: 20260524_0022
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260524_0023"
down_revision = "20260524_0022"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("agent_tasks", "bundle_id"):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("agent_tasks") as batch_op:
            batch_op.drop_column("bundle_id")
    else:
        op.drop_column("agent_tasks", "bundle_id")


def downgrade() -> None:
    # Removed task coupling is intentionally not reconstructed.
    pass
