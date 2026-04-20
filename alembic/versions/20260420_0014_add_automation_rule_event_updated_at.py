"""add updated_at to automation rule events

Revision ID: 20260420_0014
Revises: 20260420_0013
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa


revision = "20260420_0014"
down_revision = "20260420_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automation_rule_events", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute(sa.text("UPDATE automation_rule_events SET updated_at = created_at WHERE updated_at IS NULL"))


def downgrade() -> None:
    op.drop_column("automation_rule_events", "updated_at")
