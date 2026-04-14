"""add agent task visibility and runtime tracking fields

Revision ID: 20260409_0008
Revises: 20260408_0007
Create Date: 2026-04-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260409_0008"
down_revision = "20260408_0007"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(column.get("name") == column_name for column in columns)


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

    needs_owner_user_id = not _has_column("agent_tasks", "owner_user_id")
    needs_created_by_user_id = not _has_column("agent_tasks", "created_by_user_id")

    owner_user_id_will_exist = needs_owner_user_id or _has_column("agent_tasks", "owner_user_id")
    created_by_user_id_will_exist = needs_created_by_user_id or _has_column("agent_tasks", "created_by_user_id")

    needs_owner_user_fk = owner_user_id_will_exist and not _has_fk("agent_tasks", ["owner_user_id"], "users")
    needs_created_by_user_fk = created_by_user_id_will_exist and not _has_fk("agent_tasks", ["created_by_user_id"], "users")

    if dialect == "sqlite":
        if needs_owner_user_id or needs_created_by_user_id or needs_owner_user_fk or needs_created_by_user_fk:
            with op.batch_alter_table("agent_tasks") as batch_op:
                if needs_owner_user_id:
                    batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
                if needs_created_by_user_id:
                    batch_op.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
                if needs_owner_user_fk:
                    batch_op.create_foreign_key(
                        "fk_agent_tasks_owner_user_id_users",
                        "users",
                        ["owner_user_id"],
                        ["id"],
                    )
                if needs_created_by_user_fk:
                    batch_op.create_foreign_key(
                        "fk_agent_tasks_created_by_user_id_users",
                        "users",
                        ["created_by_user_id"],
                        ["id"],
                    )
    else:
        if needs_owner_user_id:
            op.add_column(
                "agent_tasks",
                sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            )
        if needs_created_by_user_id:
            op.add_column(
                "agent_tasks",
                sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            )
    if not _has_column("agent_tasks", "runtime_request_id"):
        op.add_column("agent_tasks", sa.Column("runtime_request_id", sa.String(length=128), nullable=True))
    if not _has_column("agent_tasks", "summary"):
        op.add_column("agent_tasks", sa.Column("summary", sa.Text(), nullable=True))
    if not _has_column("agent_tasks", "error_message"):
        op.add_column("agent_tasks", sa.Column("error_message", sa.Text(), nullable=True))
    if not _has_column("agent_tasks", "started_at"):
        op.add_column("agent_tasks", sa.Column("started_at", sa.DateTime(), nullable=True))
    if not _has_column("agent_tasks", "finished_at"):
        op.add_column("agent_tasks", sa.Column("finished_at", sa.DateTime(), nullable=True))

    if not _has_index("agent_tasks", "ix_agent_tasks_owner_user_id"):
        op.create_index("ix_agent_tasks_owner_user_id", "agent_tasks", ["owner_user_id"])
    if not _has_index("agent_tasks", "ix_agent_tasks_created_by_user_id"):
        op.create_index("ix_agent_tasks_created_by_user_id", "agent_tasks", ["created_by_user_id"])
    if not _has_index("agent_tasks", "ix_agent_tasks_runtime_request_id"):
        op.create_index("ix_agent_tasks_runtime_request_id", "agent_tasks", ["runtime_request_id"])

    op.execute(
        sa.text(
            """
            UPDATE agent_tasks
            SET owner_user_id = (
                SELECT agents.owner_user_id
                FROM agents
                WHERE agents.id = agent_tasks.assignee_agent_id
            )
            WHERE owner_user_id IS NULL
            """
        )
    )


def downgrade() -> None:
    for index_name in [
        "ix_agent_tasks_runtime_request_id",
        "ix_agent_tasks_created_by_user_id",
        "ix_agent_tasks_owner_user_id",
    ]:
        if _has_index("agent_tasks", index_name):
            op.drop_index(index_name, table_name="agent_tasks")

    for column_name in [
        "finished_at",
        "started_at",
        "error_message",
        "summary",
        "runtime_request_id",
    ]:
        if _has_column("agent_tasks", column_name):
            op.drop_column("agent_tasks", column_name)

    if op.get_bind().dialect.name == "sqlite":
        needs_owner_fk_drop = _has_fk("agent_tasks", ["owner_user_id"], "users")
        needs_created_by_fk_drop = _has_fk("agent_tasks", ["created_by_user_id"], "users")
        needs_owner_col_drop = _has_column("agent_tasks", "owner_user_id")
        needs_created_by_col_drop = _has_column("agent_tasks", "created_by_user_id")

        if needs_owner_fk_drop or needs_created_by_fk_drop or needs_owner_col_drop or needs_created_by_col_drop:
            with op.batch_alter_table("agent_tasks") as batch_op:
                if needs_owner_fk_drop:
                    batch_op.drop_constraint("fk_agent_tasks_owner_user_id_users", type_="foreignkey")
                if needs_created_by_fk_drop:
                    batch_op.drop_constraint("fk_agent_tasks_created_by_user_id_users", type_="foreignkey")
                if needs_created_by_col_drop:
                    batch_op.drop_column("created_by_user_id")
                if needs_owner_col_drop:
                    batch_op.drop_column("owner_user_id")
    else:
        if _has_column("agent_tasks", "created_by_user_id"):
            op.drop_column("agent_tasks", "created_by_user_id")
        if _has_column("agent_tasks", "owner_user_id"):
            op.drop_column("agent_tasks", "owner_user_id")
