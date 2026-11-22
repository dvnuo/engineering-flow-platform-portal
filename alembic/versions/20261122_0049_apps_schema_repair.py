"""apps: the columns an early 0048 did not create

Revision ID: 20261122_0049
Revises: 20261114_0048
Create Date: 2026-11-22

Revision 0048 gained three columns after builds of the Apps branch had
already run it: ``apps.data_purged_at`` and
``app_releases.template_version_id`` (2026-11-15), then ``apps.icon_hash``
(2026-11-21). A database those builds brought to 0048 is recorded as being
there, so 0048 does not run again, and it would leave the existing tables
alone if it did. Every read of ``apps`` then fails with "no such column".

This revision adds whichever of the three is missing and gives an icon
stored without its hash the SHA-1 of its bytes, as a deploy writes it, so
the icon keeps showing. A database 0048 created whole already has the
columns, and nothing changes there.
"""

import hashlib

from alembic import op
import sqlalchemy as sa


revision = "20261122_0049"
down_revision = "20261114_0048"
branch_labels = None
depends_on = None

# (table, column name, column type), each as 0048 creates it today; every
# one is nullable.
REPAIRED_COLUMNS = (
    ("apps", "icon_hash", lambda: sa.String(length=40)),
    ("apps", "data_purged_at", lambda: sa.DateTime()),
    ("app_releases", "template_version_id", lambda: sa.String(length=36)),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table_name: str) -> set[str]:
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    added_icon_hash = False
    for table_name, column_name, column_type in REPAIRED_COLUMNS:
        columns = _columns(table_name)
        if columns and column_name not in columns:
            op.add_column(table_name, sa.Column(column_name, column_type(), nullable=True))
            added_icon_hash = added_icon_hash or (table_name, column_name) == ("apps", "icon_hash")

    if added_icon_hash:
        bind = op.get_bind()
        rows = bind.execute(sa.text("SELECT id, icon_data FROM apps WHERE icon_data IS NOT NULL AND icon_hash IS NULL")).fetchall()
        for app_id, icon_data in rows:
            bind.execute(
                sa.text("UPDATE apps SET icon_hash = :icon_hash WHERE id = :id"),
                {"icon_hash": hashlib.sha1(bytes(icon_data)).hexdigest(), "id": app_id},
            )


def downgrade() -> None:
    # The columns are part of the schema 0048 creates, so going back to 0048
    # keeps them.
    pass
