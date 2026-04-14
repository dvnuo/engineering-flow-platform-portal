"""user scoped runtime profiles with default semantics

Revision ID: 20260414_0010
Revises: 20260413_0009
Create Date: 2026-04-14
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "20260414_0010"
down_revision = "20260413_0009"
branch_labels = None
depends_on = None


def _column_names(inspector, table_name: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table_name)}


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _drop_legacy_runtime_profile_name_uniqueness(inspector) -> None:
    indexes = inspector.get_indexes("runtime_profiles")
    for index in indexes:
        if index.get("unique") and index.get("column_names") == ["name"]:
            op.drop_index(index["name"], table_name="runtime_profiles")

    uniques = inspector.get_unique_constraints("runtime_profiles")
    for constraint in uniques:
        cols = constraint.get("column_names") or []
        if cols == ["name"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "runtime_profiles", type_="unique")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    runtime_columns = _column_names(inspector, "runtime_profiles")
    if "owner_user_id" not in runtime_columns:
        op.add_column("runtime_profiles", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    if "is_default" not in runtime_columns:
        op.add_column("runtime_profiles", sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()))

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "runtime_profiles", "ix_runtime_profiles_owner_user_id"):
        op.create_index("ix_runtime_profiles_owner_user_id", "runtime_profiles", ["owner_user_id"], unique=False)

    inspector = sa.inspect(bind)
    _drop_legacy_runtime_profile_name_uniqueness(inspector)
    inspector = sa.inspect(bind)
    if bind.dialect.name == "sqlite":
        if not _has_index(inspector, "runtime_profiles", "uq_runtime_profiles_owner_name"):
            op.create_index("uq_runtime_profiles_owner_name", "runtime_profiles", ["owner_user_id", "name"], unique=True)
    elif not any((uc.get("name") == "uq_runtime_profiles_owner_name") for uc in inspector.get_unique_constraints("runtime_profiles")):
        op.create_unique_constraint("uq_runtime_profiles_owner_name", "runtime_profiles", ["owner_user_id", "name"])

    metadata = sa.MetaData()
    users = sa.Table("users", metadata, autoload_with=bind)
    agents = sa.Table("agents", metadata, autoload_with=bind)
    runtime_profiles = sa.Table("runtime_profiles", metadata, autoload_with=bind)

    now = datetime.utcnow()

    user_rows = bind.execute(
        sa.select(users.c.id, users.c.role, users.c.created_at).order_by(users.c.created_at.asc(), users.c.id.asc())
    ).fetchall()
    user_ids = [int(row.id) for row in user_rows]
    admin_ids = [int(row.id) for row in user_rows if (row.role or "") == "admin"]
    fallback_owner_id = (admin_ids[0] if admin_ids else (user_ids[0] if user_ids else None))

    profile_rows = bind.execute(
        sa.select(
            runtime_profiles.c.id,
            runtime_profiles.c.name,
            runtime_profiles.c.description,
            runtime_profiles.c.config_json,
            runtime_profiles.c.revision,
            runtime_profiles.c.created_at,
            runtime_profiles.c.updated_at,
        )
    ).fetchall()

    for profile in profile_rows:
        owners = bind.execute(
            sa.select(sa.distinct(agents.c.owner_user_id))
            .where(agents.c.runtime_profile_id == profile.id)
            .where(agents.c.owner_user_id.is_not(None))
            .order_by(agents.c.owner_user_id.asc())
        ).scalars().all()

        if not owners:
            if fallback_owner_id is None:
                continue
            bind.execute(
                runtime_profiles.update().where(runtime_profiles.c.id == profile.id).values(owner_user_id=fallback_owner_id)
            )
            continue

        primary_owner_id = int(owners[0])
        bind.execute(
            runtime_profiles.update().where(runtime_profiles.c.id == profile.id).values(owner_user_id=primary_owner_id)
        )

        for owner_id in owners[1:]:
            clone_id = str(uuid4())
            bind.execute(
                runtime_profiles.insert().values(
                    id=clone_id,
                    owner_user_id=int(owner_id),
                    is_default=False,
                    name=profile.name,
                    description=profile.description,
                    config_json=profile.config_json or "{}",
                    revision=int(profile.revision or 1),
                    created_at=profile.created_at or now,
                    updated_at=profile.updated_at or now,
                )
            )
            bind.execute(
                agents.update()
                .where(agents.c.runtime_profile_id == profile.id)
                .where(agents.c.owner_user_id == int(owner_id))
                .values(runtime_profile_id=clone_id)
            )

    for user_id in user_ids:
        profile_count = bind.execute(
            sa.select(sa.func.count()).select_from(runtime_profiles).where(runtime_profiles.c.owner_user_id == user_id)
        ).scalar_one()
        if int(profile_count or 0) > 0:
            continue
        bind.execute(
            runtime_profiles.insert().values(
                id=str(uuid4()),
                owner_user_id=user_id,
                is_default=True,
                name="Default Runtime",
                description="Auto-provisioned default runtime profile",
                config_json="{}",
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )

    for user_id in user_ids:
        profiles = bind.execute(
            sa.select(runtime_profiles.c.id, runtime_profiles.c.created_at)
            .where(runtime_profiles.c.owner_user_id == user_id)
            .order_by(runtime_profiles.c.created_at.asc(), runtime_profiles.c.id.asc())
        ).fetchall()
        if not profiles:
            continue

        usage = {
            row.id: int(row.cnt or 0)
            for row in bind.execute(
                sa.select(agents.c.runtime_profile_id.label("id"), sa.func.count().label("cnt"))
                .where(agents.c.owner_user_id == user_id)
                .where(agents.c.runtime_profile_id.is_not(None))
                .group_by(agents.c.runtime_profile_id)
            ).fetchall()
        }

        winner = sorted(
            profiles,
            key=lambda row: (-usage.get(row.id, 0), row.created_at or now, row.id),
        )[0]

        bind.execute(
            runtime_profiles.update()
            .where(runtime_profiles.c.owner_user_id == user_id)
            .values(is_default=False)
        )
        bind.execute(runtime_profiles.update().where(runtime_profiles.c.id == winner.id).values(is_default=True))

    agent_rows_without_profile = bind.execute(
        sa.select(agents.c.id, agents.c.owner_user_id).where(agents.c.runtime_profile_id.is_(None))
    ).fetchall()
    for agent_row in agent_rows_without_profile:
        default_id = bind.execute(
            sa.select(runtime_profiles.c.id)
            .where(runtime_profiles.c.owner_user_id == agent_row.owner_user_id)
            .where(runtime_profiles.c.is_default.is_(True))
            .order_by(runtime_profiles.c.created_at.asc(), runtime_profiles.c.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        if default_id:
            bind.execute(agents.update().where(agents.c.id == agent_row.id).values(runtime_profile_id=default_id))

    if bind.dialect.name != "sqlite":
        op.alter_column("runtime_profiles", "owner_user_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if bind.dialect.name == "sqlite":
        if _has_index(inspector, "runtime_profiles", "uq_runtime_profiles_owner_name"):
            op.drop_index("uq_runtime_profiles_owner_name", table_name="runtime_profiles")
    elif any(uc.get("name") == "uq_runtime_profiles_owner_name" for uc in inspector.get_unique_constraints("runtime_profiles")):
        op.drop_constraint("uq_runtime_profiles_owner_name", "runtime_profiles", type_="unique")
    if _has_index(inspector, "runtime_profiles", "ix_runtime_profiles_owner_user_id"):
        op.drop_index("ix_runtime_profiles_owner_user_id", table_name="runtime_profiles")

    runtime_columns = _column_names(inspector, "runtime_profiles")
    if "owner_user_id" in runtime_columns:
        op.drop_column("runtime_profiles", "owner_user_id")
    if "is_default" in runtime_columns:
        op.drop_column("runtime_profiles", "is_default")

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "runtime_profiles", "ix_runtime_profiles_name"):
        op.create_index("ix_runtime_profiles_name", "runtime_profiles", ["name"], unique=True)
