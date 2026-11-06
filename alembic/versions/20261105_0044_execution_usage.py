"""execution usage: the tokens, credits and model of each run, kept in the Portal

Revision ID: 20261105_0044
Revises: 20261105_0043
Create Date: 2026-11-05

Both runtimes report what a chat or task cost on its final payload (``usage``
with the token counts and the credits estimate, and the model that answered),
but the Portal only kept them in the runtime's own ``usage.jsonl``, reachable
while that pod runs. ``agent_executions`` now keeps them per run, so member
usage can be added up per period without any assistant running
(app/services/member_management_service.py). Older rows stay NULL; the index
serves the per-member period sums.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261105_0044"
down_revision = "20261105_0043"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("model", sa.String(length=128), nullable=True),
    sa.Column("input_tokens", sa.Integer(), nullable=True),
    sa.Column("output_tokens", sa.Integer(), nullable=True),
    sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
    sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
    sa.Column("estimated_credits", sa.Float(), nullable=True),
)
INDEX_NAME = "ix_agent_executions_created_by_created_at"


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    existing = _columns("agent_executions")
    for column in COLUMNS:
        if column.name not in existing:
            op.add_column("agent_executions", column.copy())
    if not _has_index("agent_executions", INDEX_NAME):
        op.create_index(INDEX_NAME, "agent_executions", ["created_by_user_id", "created_at"], unique=False)


def downgrade() -> None:
    if _has_index("agent_executions", INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="agent_executions")
    existing = _columns("agent_executions")
    for column in reversed(COLUMNS):
        if column.name in existing:
            op.drop_column("agent_executions", column.name)
