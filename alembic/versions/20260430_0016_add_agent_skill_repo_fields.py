"""add agent skill repo fields

Revision ID: 20260430_0016
Revises: 20260428_0015
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa

revision = "20260430_0016"
down_revision = "20260428_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("skill_repo_url", sa.String(length=512), nullable=True))
    op.add_column("agents", sa.Column("skill_branch", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "skill_branch")
    op.drop_column("agents", "skill_repo_url")
