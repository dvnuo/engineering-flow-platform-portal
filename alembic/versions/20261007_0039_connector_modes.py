"""per-connector mode: follow the administrator's Default connectors or keep own values

Revision ID: 20261007_0039
Revises: 20260930_0038
Create Date: 2026-10-07

A member's settings row used to receive a copy of the administrator's Default
connectors once, when it was created, and never again. Each settings
connector now has a mode: ``system`` (the values come from the current
Default connectors and follow the administrator's later changes) or
``custom`` (the member's own values). The modes live in
``runtime_profiles.connector_modes_json``; NULL marks a row the Portal has
not classified yet, which it does on startup (``RuntimeProfileService
.backfill_connector_modes``): a connector whose values equal the current
Default connectors follows them, one whose values differ stays custom, so
nobody's settings change on upgrade.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261007_0039"
down_revision = "20260930_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runtime_profiles", sa.Column("connector_modes_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runtime_profiles", "connector_modes_json")
