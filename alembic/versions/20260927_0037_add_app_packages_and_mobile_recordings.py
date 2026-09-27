"""add app packages and mobile recordings

Revision ID: 20260927_0037
Revises: 20260925_0036
Create Date: 2026-09-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260927_0037"
down_revision = "20260925_0036"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def upgrade() -> None:
    # Mobile app builds a member uploaded to BrowserStack through Portal. The
    # file stays on BrowserStack; this is the reference recordings and runs
    # pick it by.
    if not _has_table("app_packages"):
        op.create_table(
            "app_packages",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column("platform", sa.String(length=16), nullable=False),
            sa.Column("app_url", sa.String(length=255), nullable=False),
            sa.Column("custom_id", sa.String(length=100), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("note", sa.String(length=255), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="upload"),
            sa.Column("source_url", sa.String(length=1024), nullable=True),
            sa.Column("uploaded_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for name, columns in (
        ("ix_app_packages_owner_user_id", ["owner_user_id"]),
        ("ix_app_packages_owner_uploaded", ["owner_user_id", "uploaded_at"]),
    ):
        if not _has_index("app_packages", name):
            op.create_index(name, "app_packages", columns, unique=False)

    # Recording sessions of the hosted Appium Inspector and the WebDriver
    # commands they logged.
    if not _has_table("mobile_recordings"):
        op.create_table(
            "mobile_recordings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("session_id", sa.String(length=128), nullable=False),
            sa.Column("hub_url", sa.String(length=512), nullable=False),
            sa.Column("platform", sa.String(length=16), nullable=False, server_default=""),
            sa.Column("device", sa.String(length=128), nullable=True),
            sa.Column("run_id", sa.String(length=128), nullable=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("segment", sa.String(length=128), nullable=False, server_default="segment-1"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("viewport_width", sa.Integer(), nullable=True),
            sa.Column("viewport_height", sa.Integer(), nullable=True),
            sa.Column("last_event_at", sa.DateTime(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for name, columns in (
        ("ix_mobile_recordings_owner_user_id", ["owner_user_id"]),
        ("ix_mobile_recordings_agent_id", ["agent_id"]),
        ("ix_mobile_recordings_owner_agent_status", ["owner_user_id", "agent_id", "status"]),
    ):
        if not _has_index("mobile_recordings", name):
            op.create_index(name, "mobile_recordings", columns, unique=False)

    if not _has_table("mobile_recording_events"):
        op.create_table(
            "mobile_recording_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("recording_id", sa.String(length=36), nullable=False),
            sa.Column("segment", sa.String(length=128), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("method", sa.String(length=8), nullable=False),
            sa.Column("path", sa.String(length=512), nullable=False),
            sa.Column("request_json", sa.Text(), nullable=True),
            sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("element_id", sa.String(length=128), nullable=True),
            sa.Column("element_json", sa.Text(), nullable=True),
            sa.Column("element_ids_json", sa.Text(), nullable=True),
            sa.Column("secret", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["recording_id"], ["mobile_recordings.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    for name, columns in (
        ("ix_mobile_recording_events_recording_id", ["recording_id"]),
        ("ix_mobile_recording_events_recording_segment", ["recording_id", "segment", "seq"]),
    ):
        if not _has_index("mobile_recording_events", name):
            op.create_index(name, "mobile_recording_events", columns, unique=False)


def downgrade() -> None:
    for table, indexes in (
        ("mobile_recording_events", ("ix_mobile_recording_events_recording_segment", "ix_mobile_recording_events_recording_id")),
        ("mobile_recordings", ("ix_mobile_recordings_owner_agent_status", "ix_mobile_recordings_agent_id", "ix_mobile_recordings_owner_user_id")),
        ("app_packages", ("ix_app_packages_owner_uploaded", "ix_app_packages_owner_user_id")),
    ):
        if _has_table(table):
            for index in indexes:
                if _has_index(table, index):
                    op.drop_index(index, table_name=table)
            op.drop_table(table)
