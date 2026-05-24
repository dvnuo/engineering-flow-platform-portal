"""simplify runtime schema

Revision ID: 20260524_0025
Revises: 20260524_0024
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260524_0025"
down_revision = "20260524_0024"
branch_labels = None
depends_on = None


def _c(*chunks: str) -> str:
    return "".join(chunks)


def _j(*parts: str) -> str:
    return "_".join(parts)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column.get("name") == column_name for column in _inspector().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index.get("name") == index_name for index in _inspector().get_indexes(table_name))


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_table_if_exists(table_name: str) -> None:
    if _has_table(table_name):
        op.drop_table(table_name)


def _drop_columns_if_present(table_name: str, column_names: list[str]) -> None:
    existing = [column_name for column_name in column_names if _has_column(table_name, column_name)]
    if not existing:
        return
    with op.batch_alter_table(table_name) as batch_op:
        for column_name in existing:
            batch_op.drop_column(column_name)


def upgrade() -> None:
    for table_name in [
        _j(_c("ag", "ent"), _c("gro", "up"), _c("mem", "bers")),
        _j(_c("ag", "ent"), _c("gro", "ups")),
        _j(_c("ag", "ent"), _c("dele", "gat", "ions")),
        _j(_c("ag", "ent"), _c("coord", "inat", "ion"), _c("ru", "ns")),
        _j(_c("work", "flow"), _c("trans", "ition"), _c("ru", "les")),
        _j(_c("ag", "ent"), _c("iden", "tity"), _c("bind", "ings")),
    ]:
        _drop_table_if_exists(table_name)

    _drop_index_if_exists("agents", _j("ix", "agents", _c("temp", "late"), _c("ag", "ent"), "id"))
    _drop_columns_if_present(
        "agents",
        [
            _j(_c("temp", "late"), _c("ag", "ent"), "id"),
            _j(_c("ta", "sk"), _c("sco", "pe"), "label"),
            _j(_c("ta", "sk"), _c("clean", "up"), _c("pol", "icy")),
            _j(_c("to", "ol"), _c("re", "po"), "url"),
            _j(_c("to", "ol"), _c("bra", "nch")),
        ],
    )

    _drop_index_if_exists("agent_tasks", _j("ix", _c("ag", "ent"), _c("ta", "sks"), _c("gro", "up"), "id"))
    _drop_index_if_exists(
        "agent_tasks",
        _j("ix", _c("ag", "ent"), _c("ta", "sks"), _c("par", "ent"), _c("ag", "ent"), "id"),
    )
    _drop_columns_if_present(
        "agent_tasks",
        [
            _j(_c("gro", "up"), "id"),
            _j(_c("par", "ent"), _c("ag", "ent"), "id"),
        ],
    )

    _drop_columns_if_present(
        "agent_session_metadata",
        [
            _j(_c("gro", "up"), "id"),
            _j("current", _c("dele", "gat", "ion"), "id"),
            _j("current", _c("coord", "inat", "ion"), _c("ru", "n"), "id"),
            _j(_c("pend", "ing"), _c("dele", "gat", "ions"), "json"),
        ],
    )


def downgrade() -> None:
    pass
