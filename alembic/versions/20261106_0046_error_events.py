"""error events: failures the Portal saw, kept for Administration > Errors

Revision ID: 20261106_0046
Revises: 20261105_0045
Create Date: 2026-11-06

``error_events`` holds one row per failure the Portal recorded: a run that
ended failed or stale, an assistant whose runtime failed, a connector Test
that failed, a Portal response of 500 or more or an unhandled exception, and
a warning or error from the Portal's own loggers
(app/services/error_log_service.py). Guarded like earlier revisions: a
database that already has the table is left alone.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261106_0046"
down_revision = "20261105_0045"
branch_labels = None
depends_on = None

TABLE = "error_events"
INDEXES = (
    ("ix_error_events_last_seen", ["last_seen_at"]),
    ("ix_error_events_fingerprint_seen", ["fingerprint", "last_seen_at"]),
    ("ix_error_events_source_seen", ["source", "last_seen_at"]),
    ("ix_error_events_user", ["user_id"]),
    ("ix_error_events_agent", ["agent_id"]),
)


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    if TABLE in _inspector().get_table_names():
        return
    op.create_table(
        "error_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="error"),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("agent_name", sa.String(length=255), nullable=True),
        sa.Column("execution_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("runtime_type", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=True),
        sa.Column("request_path", sa.String(length=255), nullable=True),
        sa.Column("logger_name", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
    )
    for name, columns in INDEXES:
        op.create_index(name, TABLE, columns, unique=False)


def downgrade() -> None:
    if TABLE not in _inspector().get_table_names():
        return
    for name, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)
