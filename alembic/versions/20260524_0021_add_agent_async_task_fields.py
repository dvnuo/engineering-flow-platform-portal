"""add agent async task fields

Revision ID: 20260524_0021
Revises: 20260510_0020
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260524_0021"
down_revision = "20260510_0020"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _has_fk(table_name: str, constrained_columns: list[str], referred_table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys(table_name):
        if fk.get("referred_table") == referred_table and fk.get("constrained_columns") == constrained_columns:
            return True
    return False


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    needs_title = not _has_column("agent_tasks", "title")
    needs_skill_name = not _has_column("agent_tasks", "skill_name")
    needs_parent_task_id = not _has_column("agent_tasks", "parent_task_id")
    needs_root_task_id = not _has_column("agent_tasks", "root_task_id")
    needs_task_session_id = not _has_column("agent_tasks", "task_session_id")
    needs_parent_fk = (needs_parent_task_id or _has_column("agent_tasks", "parent_task_id")) and not _has_fk(
        "agent_tasks", ["parent_task_id"], "agent_tasks"
    )
    needs_root_fk = (needs_root_task_id or _has_column("agent_tasks", "root_task_id")) and not _has_fk(
        "agent_tasks", ["root_task_id"], "agent_tasks"
    )

    if dialect == "sqlite":
        needs_sqlite_batch = any(
            [
                needs_title,
                needs_skill_name,
                needs_parent_task_id,
                needs_root_task_id,
                needs_task_session_id,
                needs_parent_fk,
                needs_root_fk,
            ]
        )
        if needs_sqlite_batch:
            with op.batch_alter_table("agent_tasks") as batch_op:
                if needs_title:
                    batch_op.add_column(sa.Column("title", sa.String(length=255), nullable=True))
                if needs_skill_name:
                    batch_op.add_column(sa.Column("skill_name", sa.String(length=128), nullable=True))
                if needs_parent_task_id:
                    batch_op.add_column(sa.Column("parent_task_id", sa.String(length=36), nullable=True))
                if needs_root_task_id:
                    batch_op.add_column(sa.Column("root_task_id", sa.String(length=36), nullable=True))
                if needs_task_session_id:
                    batch_op.add_column(sa.Column("task_session_id", sa.String(length=128), nullable=True))
                if needs_parent_fk:
                    batch_op.create_foreign_key(
                        "fk_agent_tasks_parent_task_id_agent_tasks",
                        "agent_tasks",
                        ["parent_task_id"],
                        ["id"],
                    )
                if needs_root_fk:
                    batch_op.create_foreign_key(
                        "fk_agent_tasks_root_task_id_agent_tasks",
                        "agent_tasks",
                        ["root_task_id"],
                        ["id"],
                    )
    else:
        if needs_title:
            op.add_column("agent_tasks", sa.Column("title", sa.String(length=255), nullable=True))
        if needs_skill_name:
            op.add_column("agent_tasks", sa.Column("skill_name", sa.String(length=128), nullable=True))
        if needs_parent_task_id:
            op.add_column(
                "agent_tasks",
                sa.Column("parent_task_id", sa.String(length=36), sa.ForeignKey("agent_tasks.id"), nullable=True),
            )
        if needs_root_task_id:
            op.add_column(
                "agent_tasks",
                sa.Column("root_task_id", sa.String(length=36), sa.ForeignKey("agent_tasks.id"), nullable=True),
            )
        if needs_task_session_id:
            op.add_column("agent_tasks", sa.Column("task_session_id", sa.String(length=128), nullable=True))

    for index_name, columns in [
        ("ix_agent_tasks_skill_name", ["skill_name"]),
        ("ix_agent_tasks_parent_task_id", ["parent_task_id"]),
        ("ix_agent_tasks_root_task_id", ["root_task_id"]),
        ("ix_agent_tasks_task_session_id", ["task_session_id"]),
    ]:
        if not _has_index("agent_tasks", index_name):
            op.create_index(index_name, "agent_tasks", columns)


def downgrade() -> None:
    for index_name in [
        "ix_agent_tasks_task_session_id",
        "ix_agent_tasks_root_task_id",
        "ix_agent_tasks_parent_task_id",
        "ix_agent_tasks_skill_name",
    ]:
        if _has_index("agent_tasks", index_name):
            op.drop_index(index_name, table_name="agent_tasks")

    if op.get_bind().dialect.name == "sqlite":
        needs_parent_fk_drop = _has_fk("agent_tasks", ["parent_task_id"], "agent_tasks")
        needs_root_fk_drop = _has_fk("agent_tasks", ["root_task_id"], "agent_tasks")
        columns_to_drop = [
            column_name
            for column_name in ["task_session_id", "root_task_id", "parent_task_id", "skill_name", "title"]
            if _has_column("agent_tasks", column_name)
        ]
        if needs_parent_fk_drop or needs_root_fk_drop or columns_to_drop:
            with op.batch_alter_table("agent_tasks") as batch_op:
                if needs_parent_fk_drop:
                    batch_op.drop_constraint("fk_agent_tasks_parent_task_id_agent_tasks", type_="foreignkey")
                if needs_root_fk_drop:
                    batch_op.drop_constraint("fk_agent_tasks_root_task_id_agent_tasks", type_="foreignkey")
                for column_name in columns_to_drop:
                    batch_op.drop_column(column_name)
    else:
        for column_name in ["task_session_id", "root_task_id", "parent_task_id", "skill_name", "title"]:
            if _has_column("agent_tasks", column_name):
                op.drop_column("agent_tasks", column_name)
