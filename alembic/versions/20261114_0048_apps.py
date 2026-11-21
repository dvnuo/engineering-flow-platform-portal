"""apps: members' web apps, their deployments, usage and audit trail

Revision ID: 20261114_0048
Revises: 20261110_0047
Create Date: 2026-11-14

Seven tables for the Apps feature (app/models/app.py): ``apps`` (the
registry: repository, owner, audience, token, what the app may use),
``app_releases`` (deployments and rollbacks with their steps),
``app_template_versions`` (tags published as template versions),
``app_executions`` (one row per SDK call, for the daily budget and usage),
``app_traffic_daily`` and ``app_visits`` (what the gateway counted) and
``app_audit_events`` (who did what to an app, and what the host reported).

``usage_daily_rollups`` gains ``app_id`` ("" for an assistant's runs, like
the other dimension sentinels) and its unique constraint takes the column
in, so an app's model usage rolls up next to the assistants' under
``runtime_type = "app"``. Guarded like earlier revisions: a table or a
column that already exists is left alone.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261114_0048"
down_revision = "20261110_0047"
branch_labels = None
depends_on = None

ROLLUPS = "usage_daily_rollups"
ROLLUP_CONSTRAINT = "uq_usage_daily_rollups_dims"
ROLLUP_DIMS_BEFORE = ["day", "user_id", "agent_id", "runtime_type", "kind", "model"]
ROLLUP_DIMS_AFTER = ["day", "user_id", "agent_id", "app_id", "runtime_type", "kind", "model"]

# Dropped in this order (children before ``apps``).
TABLES = (
    "app_audit_events",
    "app_visits",
    "app_traffic_daily",
    "app_executions",
    "app_template_versions",
    "app_releases",
    "apps",
)


def _inspector():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _has_unique_constraint(table_name: str, name: str) -> bool:
    return any(item.get("name") == name for item in _inspector().get_unique_constraints(table_name))


def _create_apps() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_apps_owner_user_id_users"), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="private"),
        sa.Column("type", sa.String(length=16), nullable=True),
        sa.Column("repo_url", sa.String(length=512), nullable=False),
        sa.Column("repo_ref", sa.String(length=255), nullable=False),
        sa.Column("entry_dir", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("current_release_id", sa.String(length=36), nullable=True),
        sa.Column("desired_state", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_value", sa.String(length=64), nullable=False),
        sa.Column("token_last_used_at", sa.DateTime(), nullable=True),
        sa.Column("token_last_used_source", sa.String(length=16), nullable=True),
        sa.Column("visitor_key", sa.String(length=64), nullable=False),
        sa.Column("llm_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("connectors_enabled", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("connectors_write", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("env_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("template_app_id", sa.String(length=36), nullable=True),
        sa.Column("template_version_id", sa.String(length=36), nullable=True),
        sa.Column("anyone_approved_by", sa.Integer(), nullable=True),
        sa.Column("disabled_reason", sa.String(length=255), nullable=True),
        sa.Column("icon_data", sa.LargeBinary(), nullable=True),
        sa.Column("icon_content_type", sa.String(length=64), nullable=True),
        sa.Column("icon_hash", sa.String(length=40), nullable=True),
        sa.Column("daily_credits", sa.Float(), nullable=True),
        sa.Column("daily_tokens", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("data_purged_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_apps_slug"),
        sa.UniqueConstraint("token_hash", name="uq_apps_token_hash"),
    )
    op.create_index("ix_apps_owner_user_id", "apps", ["owner_user_id"], unique=False)
    op.create_index("ix_apps_visibility", "apps", ["visibility"], unique=False)


def _create_app_releases() -> None:
    op.create_table(
        "app_releases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("app_id", sa.String(length=36), sa.ForeignKey("apps.id", name="fk_app_releases_app_id_apps"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="deploy"),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("ref", sa.String(length=255), nullable=True),
        sa.Column("commit_message", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("steps_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("host_job_id", sa.String(length=64), nullable=True),
        sa.Column("template_version_id", sa.String(length=36), nullable=True),
        sa.Column("log_tail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_app_releases_app_started", "app_releases", ["app_id", "started_at"], unique=False)
    op.create_index("ix_app_releases_status", "app_releases", ["status"], unique=False)


def _create_app_template_versions() -> None:
    op.create_table(
        "app_template_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "app_id",
            sa.String(length=36),
            sa.ForeignKey("apps.id", name="fk_app_template_versions_app_id_apps"),
            nullable=False,
        ),
        sa.Column("tag", sa.String(length=255), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("published_by_user_id", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("app_id", "tag", name="uq_app_template_versions_app_tag"),
    )


def _create_app_executions() -> None:
    op.create_table(
        "app_executions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("visitor_user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("connector_type", sa.String(length=32), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("credits", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="succeeded"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_app_executions_app_created", "app_executions", ["app_id", "created_at"], unique=False)
    op.create_index("ix_app_executions_owner_created", "app_executions", ["owner_user_id", "created_at"], unique=False)


def _create_app_traffic_daily() -> None:
    op.create_table(
        "app_traffic_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("app_id", sa.String(length=36), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visitors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_4xx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_5xx", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p95_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("day", "app_id", name="uq_app_traffic_daily_day_app"),
    )
    op.create_index("ix_app_traffic_daily_app_day", "app_traffic_daily", ["app_id", "day"], unique=False)


def _create_app_visits() -> None:
    op.create_table(
        "app_visits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("last_visited_at", sa.DateTime(), nullable=True),
        sa.Column("visits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counted_day", sa.Date(), nullable=True),
        sa.Column("counted_day_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("app_id", "user_id", name="uq_app_visits_app_user"),
    )
    op.create_index("ix_app_visits_user_last", "app_visits", ["user_id", "last_visited_at"], unique=False)


def _create_app_audit_events() -> None:
    op.create_table(
        "app_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("app_id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_app_audit_events_app_created", "app_audit_events", ["app_id", "created_at"], unique=False)


# Created in this order (``apps`` before the tables that point at it).
CREATORS = (
    ("apps", _create_apps),
    ("app_releases", _create_app_releases),
    ("app_template_versions", _create_app_template_versions),
    ("app_executions", _create_app_executions),
    ("app_traffic_daily", _create_app_traffic_daily),
    ("app_visits", _create_app_visits),
    ("app_audit_events", _create_app_audit_events),
)


def upgrade() -> None:
    for table_name, create in CREATORS:
        if table_name not in _tables():
            create()

    if ROLLUPS in _tables() and "app_id" not in _columns(ROLLUPS):
        had_constraint = _has_unique_constraint(ROLLUPS, ROLLUP_CONSTRAINT)
        # Batch mode: SQLite rebuilds the table, the others alter it in place.
        with op.batch_alter_table(ROLLUPS) as batch_op:
            batch_op.add_column(sa.Column("app_id", sa.String(length=36), nullable=False, server_default=""))
            if had_constraint:
                batch_op.drop_constraint(ROLLUP_CONSTRAINT, type_="unique")
            batch_op.create_unique_constraint(ROLLUP_CONSTRAINT, ROLLUP_DIMS_AFTER)


def downgrade() -> None:
    if ROLLUPS in _tables() and "app_id" in _columns(ROLLUPS):
        # An app's rows share their other dimensions with nothing an assistant
        # wrote (runtime_type "app"), but two apps may share them with each
        # other: without app_id they would collide, so they go.
        op.execute(sa.text(f"DELETE FROM {ROLLUPS} WHERE app_id <> ''"))
        had_constraint = _has_unique_constraint(ROLLUPS, ROLLUP_CONSTRAINT)
        with op.batch_alter_table(ROLLUPS) as batch_op:
            if had_constraint:
                batch_op.drop_constraint(ROLLUP_CONSTRAINT, type_="unique")
            batch_op.drop_column("app_id")
            batch_op.create_unique_constraint(ROLLUP_CONSTRAINT, ROLLUP_DIMS_BEFORE)

    existing = _tables()
    for table_name in TABLES:
        if table_name in existing:
            op.drop_table(table_name)
