from datetime import datetime
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, Text, create_engine, select

spec = importlib.util.spec_from_file_location(
    "rp_user_scope_migration",
    Path("alembic/versions/20260414_0010_user_scoped_runtime_profiles.py"),
)
migration = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(migration)


def test_runtime_profile_user_scope_migration_backfills_and_clones():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("username", String(64), nullable=False),
        Column("nickname", String(64)),
        Column("password_hash", String(255), nullable=False),
        Column("role", String(16), nullable=False),
        Column("is_active", Boolean, nullable=False, default=True),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    runtime_profiles = Table(
        "runtime_profiles",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("name", String(128), nullable=False),
        Column("description", Text),
        Column("config_json", Text, nullable=False),
        Column("revision", Integer, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    )
    agents = Table(
        "agents",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_user_id", Integer, ForeignKey("users.id"), nullable=False),
        Column("runtime_profile_id", String(36), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            users.insert(),
            [
                {"id": 1, "username": "admin", "nickname": "admin", "password_hash": "x", "role": "admin", "is_active": True, "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1)},
                {"id": 2, "username": "u2", "nickname": "u2", "password_hash": "x", "role": "user", "is_active": True, "created_at": datetime(2026, 1, 2), "updated_at": datetime(2026, 1, 2)},
                {"id": 3, "username": "u3", "nickname": "u3", "password_hash": "x", "role": "user", "is_active": True, "created_at": datetime(2026, 1, 3), "updated_at": datetime(2026, 1, 3)},
            ],
        )
        conn.execute(
            runtime_profiles.insert(),
            [
                {"id": "rp-shared", "name": "Shared", "description": "shared", "config_json": "{}", "revision": 1, "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1)},
                {"id": "rp-unbound", "name": "Unbound", "description": "unbound", "config_json": "{}", "revision": 1, "created_at": datetime(2026, 1, 4), "updated_at": datetime(2026, 1, 4)},
            ],
        )
        conn.execute(
            agents.insert(),
            [
                {"id": "a1", "owner_user_id": 1, "runtime_profile_id": "rp-shared"},
                {"id": "a2", "owner_user_id": 2, "runtime_profile_id": "rp-shared"},
                {"id": "a3", "owner_user_id": 3, "runtime_profile_id": None},
            ],
        )

    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        ops = Operations(context)
        original_op = migration.op
        migration.op = ops
        try:
            migration.upgrade()
        finally:
            migration.op = original_op

    upgraded_meta = MetaData()
    upgraded_runtime_profiles = Table("runtime_profiles", upgraded_meta, autoload_with=engine)
    upgraded_agents = Table("agents", upgraded_meta, autoload_with=engine)

    with engine.connect() as conn:
        shared_owner = conn.execute(
            select(upgraded_runtime_profiles.c.owner_user_id).where(upgraded_runtime_profiles.c.id == "rp-shared")
        ).scalar_one()
        assert shared_owner == 1

        user2_profiles = conn.execute(
            select(upgraded_runtime_profiles.c.id).where(upgraded_runtime_profiles.c.owner_user_id == 2)
        ).scalars().all()
        assert user2_profiles

        user3_default = conn.execute(
            select(upgraded_runtime_profiles.c.id)
            .where(upgraded_runtime_profiles.c.owner_user_id == 3)
            .where(upgraded_runtime_profiles.c.is_default.is_(True))
        ).scalar_one()

        a2_profile = conn.execute(select(upgraded_agents.c.runtime_profile_id).where(upgraded_agents.c.id == "a2")).scalar_one()
        assert a2_profile != "rp-shared"

        a3_profile = conn.execute(select(upgraded_agents.c.runtime_profile_id).where(upgraded_agents.c.id == "a3")).scalar_one()
        assert a3_profile == user3_default
