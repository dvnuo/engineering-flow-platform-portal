"""usage rollups: what Copilot charged, why a run ended, and one row per day for the Usage panel

Revision ID: 20261105_0045
Revises: 20261105_0044
Create Date: 2026-11-05

``agent_executions`` gains ``actual_credits`` (Copilot's own charge when the
native runtime reports one next to its estimate), ``credits_source``
(``copilot_usage`` or ``estimate``) and ``terminal_reason`` (why a run ended
short: a budget pause, a step cap). ``usage_daily_rollups`` holds one row per
day, member, assistant, runtime, kind and model, recomputed from the
executions by ``app/services/usage_rollup_service.py``; the administration
Usage panel reads it and the retention job folds old executions into it before
deleting them. Both halves are guarded like earlier revisions, so a database
that already has them is left alone.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261105_0045"
down_revision = "20261105_0044"
branch_labels = None
depends_on = None

EXECUTION_COLUMNS = (
    sa.Column("actual_credits", sa.Float(), nullable=True),
    sa.Column("credits_source", sa.String(length=32), nullable=True),
    sa.Column("terminal_reason", sa.String(length=64), nullable=True),
)


def _inspector():
    return sa.inspect(op.get_bind())


def _columns(table_name: str) -> set[str]:
    inspector = _inspector()
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    existing = _columns("agent_executions")
    for column in EXECUTION_COLUMNS:
        if column.name not in existing:
            op.add_column("agent_executions", column.copy())

    if "usage_daily_rollups" not in _inspector().get_table_names():
        op.create_table(
            "usage_daily_rollups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("agent_id", sa.String(length=36), nullable=False, server_default=""),
            sa.Column("runtime_type", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("executions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("cached_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("estimated_credits", sa.Float(), nullable=False, server_default="0"),
            sa.Column("actual_credits", sa.Float(), nullable=False, server_default="0"),
            sa.Column("credits", sa.Float(), nullable=False, server_default="0"),
            sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("day", "user_id", "agent_id", "runtime_type", "kind", "model", name="uq_usage_daily_rollups_dims"),
        )
        op.create_index("ix_usage_daily_rollups_day", "usage_daily_rollups", ["day"], unique=False)
        op.create_index("ix_usage_daily_rollups_user_day", "usage_daily_rollups", ["user_id", "day"], unique=False)


def downgrade() -> None:
    if "usage_daily_rollups" in _inspector().get_table_names():
        op.drop_index("ix_usage_daily_rollups_user_day", table_name="usage_daily_rollups")
        op.drop_index("ix_usage_daily_rollups_day", table_name="usage_daily_rollups")
        op.drop_table("usage_daily_rollups")
    existing = _columns("agent_executions")
    for column in reversed(EXECUTION_COLUMNS):
        if column.name in existing:
            op.drop_column("agent_executions", column.name)
