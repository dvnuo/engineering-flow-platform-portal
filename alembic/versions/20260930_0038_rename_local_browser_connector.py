"""rename the local_browser connector to local_bridge

Revision ID: 20260930_0038
Revises: 20260928_0037
Create Date: 2026-09-30
"""

from alembic import op


revision = "20260930_0038"
down_revision = "20260928_0037"
branch_labels = None
depends_on = None


# The program behind this connector is the EFP local bridge (efp-bridge),
# which does browser automation and mobile recording, so the connector id is
# local_bridge. A member's stored preferences (enabled, on by default in new
# chats, bridge port) move with it.
def upgrade() -> None:
    op.execute("UPDATE user_connectors SET connector_type = 'local_bridge' WHERE connector_type = 'local_browser'")


def downgrade() -> None:
    op.execute("UPDATE user_connectors SET connector_type = 'local_browser' WHERE connector_type = 'local_bridge'")
