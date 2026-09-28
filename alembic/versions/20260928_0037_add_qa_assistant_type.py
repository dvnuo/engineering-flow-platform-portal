"""seed the QA Assistant type

Revision ID: 20260928_0037
Revises: 20260925_0036
Create Date: 2026-09-28
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "20260928_0037"
down_revision = "20260925_0036"
branch_labels = None
depends_on = None


# Like the Business, Dev, and Ops types of 20260828_0034, the QA type names
# the branch of the agents repository (its persona) and of the skills
# repository (its skill subset). Both `qa` branches must exist before this
# reaches a deployment, or assistants of this type fail to start with
# "connection settings aren't ready".
QA_ASSISTANT_TYPE = {
    "id": "qa",
    "name": "QA Assistant",
    "description": "Test design, recordings on real devices, mobile regression runs, and failure triage.",
    "icon": "flask-conical",
    "runtime_type": "native",
    "agent_settings_branch": "qa",
    "skill_branch": "qa",
    "sort_order": 40,
    "is_active": True,
}


def _has_assistant_types() -> bool:
    return "assistant_types" in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_assistant_types():
        return
    # An administrator may already have made one by hand; keep theirs.
    existing = op.get_bind().execute(
        sa.text("SELECT 1 FROM assistant_types WHERE id = :id OR name = :name"),
        {"id": QA_ASSISTANT_TYPE["id"], "name": QA_ASSISTANT_TYPE["name"]},
    ).first()
    if existing:
        return
    now = datetime.utcnow()
    op.bulk_insert(
        sa.table(
            "assistant_types",
            sa.column("id", sa.String),
            sa.column("name", sa.String),
            sa.column("description", sa.Text),
            sa.column("icon", sa.String),
            sa.column("runtime_type", sa.String),
            sa.column("agent_settings_branch", sa.String),
            sa.column("skill_branch", sa.String),
            sa.column("sort_order", sa.Integer),
            sa.column("is_active", sa.Boolean),
            sa.column("created_at", sa.DateTime),
            sa.column("updated_at", sa.DateTime),
        ),
        [{**QA_ASSISTANT_TYPE, "created_at": now, "updated_at": now}],
    )


def downgrade() -> None:
    # Assistants copy a type's settings when they are created, so removing the
    # preset leaves existing QA assistants running as they are.
    if not _has_assistant_types():
        return
    op.get_bind().execute(sa.text("DELETE FROM assistant_types WHERE id = :id"), {"id": QA_ASSISTANT_TYPE["id"]})
