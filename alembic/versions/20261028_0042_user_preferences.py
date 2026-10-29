"""per-member preferences: the Portal language and the assistant's reply language

Revision ID: 20261028_0042
Revises: 20261020_0041
Create Date: 2026-10-28

Theme and chat width live in the browser, but the language cannot: the server
renders the pages with it, the chat proxy derives the assistant's reply
language from it on every request, and members use more than one device. So
``users.preferences_json`` holds a JSON object with ``ui_locale`` (``auto``,
``en``, ``zh-Hans``, ``zh-Hant-HK``) and ``reply_language`` (``follow`` or one
of the three tags), written through ``PUT /api/auth/me/preferences`` and read
by ``app/services/locale_service.py``. Unknown keys are kept for later
preferences. The column is guarded like ``onboarding_completed_at`` was, so a
development database that already has it is left alone.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261028_0042"
down_revision = "20261020_0041"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("users", "preferences_json"):
        op.add_column("users", sa.Column("preferences_json", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "preferences_json"):
        op.drop_column("users", "preferences_json")
