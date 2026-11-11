"""groups: every member belongs to one group, which has its own Default connectors

Revision ID: 20261110_0047
Revises: 20261106_0046
Create Date: 2026-11-10

``groups`` holds the teams of the platform (app/models/group.py). A group's
Default connectors are the platform's with the connectors it sets itself
replaced by its own values; its members follow those the way they followed
the platform's. ``users.group_id`` is the member's group and
``user_allowlist.group_id`` the group an account created from that entry
joins.

One group exists from the start, the default group, and every member and
allowlist entry joins it. It follows the platform for every connector, so
what every assistant gets is exactly what it got before this revision: no
settings change and nothing restarts. Guarded like earlier revisions: a
database that already has the table or a column is left alone.
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "20261110_0047"
down_revision = "20261106_0046"
branch_labels = None
depends_on = None

TABLE = "groups"
DEFAULT_GROUP_NAME = "Default"
MEMBER_COLUMNS = (
    ("users", "group_id", "fk_users_group_id_groups"),
    ("user_allowlist", "group_id", "fk_user_allowlist_group_id_groups"),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in _inspector().get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("connector_modes_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("name", name="uq_groups_name"),
        )

    groups = sa.table(
        TABLE,
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("is_default", sa.Boolean()),
        sa.column("config_json", sa.Text()),
        sa.column("connector_modes_json", sa.Text()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    default_id = bind.execute(sa.select(groups.c.id).where(groups.c.is_default.is_(True))).scalar()
    if default_id is None:
        now = datetime.utcnow()
        bind.execute(
            groups.insert().values(
                name=DEFAULT_GROUP_NAME,
                is_default=True,
                config_json="{}",
                connector_modes_json="{}",
                created_at=now,
                updated_at=now,
            )
        )
        default_id = bind.execute(sa.select(groups.c.id).where(groups.c.is_default.is_(True))).scalar()

    for table_name, column_name, fk_name in MEMBER_COLUMNS:
        if _has_column(table_name, column_name):
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.add_column(sa.Column(column_name, sa.Integer(), nullable=True))
                batch_op.create_foreign_key(fk_name, TABLE, [column_name], ["id"])
        else:
            op.add_column(
                table_name,
                sa.Column(column_name, sa.Integer(), sa.ForeignKey(f"{TABLE}.id", name=fk_name), nullable=True),
            )
    if not any(index["name"] == "ix_users_group_id" for index in _inspector().get_indexes("users")):
        op.create_index("ix_users_group_id", "users", ["group_id"], unique=False)

    for table_name, column_name, _fk_name in MEMBER_COLUMNS:
        bind.execute(
            sa.text(f"UPDATE {table_name} SET {column_name} = :group_id WHERE {column_name} IS NULL"),
            {"group_id": default_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if any(index["name"] == "ix_users_group_id" for index in _inspector().get_indexes("users")):
        op.drop_index("ix_users_group_id", table_name="users")
    for table_name, column_name, fk_name in reversed(MEMBER_COLUMNS):
        if not _has_column(table_name, column_name):
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.drop_constraint(fk_name, type_="foreignkey")
                batch_op.drop_column(column_name)
        else:
            op.drop_constraint(fk_name, table_name, type_="foreignkey")
            op.drop_column(table_name, column_name)
    if TABLE in _inspector().get_table_names():
        op.drop_table(TABLE)
