"""per-session run settings chosen in the chat composer

Revision ID: 20261020_0040
Revises: 20261007_0039
Create Date: 2026-10-20

The model, thinking level and context size a member picks under "Run
settings" in the chat composer used to live in the page: a refresh, another
tab or another device went back to the assistant's defaults. They now live
with the session, in ``agent_session_metadata.run_settings_json`` (a JSON
object with ``model_override``, ``reasoning_effort`` and
``max_context_tokens``), written by the member through the Portal API and
filled by the chat proxy into requests that name none of their own
(``app/services/session_run_settings_service.py``).
"""

from alembic import op
import sqlalchemy as sa


revision = "20261020_0040"
down_revision = "20261007_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_session_metadata", sa.Column("run_settings_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_session_metadata", "run_settings_json")
