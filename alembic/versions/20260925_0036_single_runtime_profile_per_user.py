"""one runtime profile per member; connectors edit its sections

Revision ID: 20260925_0036
Revises: 20260913_0035
Create Date: 2026-09-25

Members used to keep several named profiles and bind each assistant to one.
Settings are now edited per connector, and every assistant of a member uses
that member's single profile. This revision:

1. keeps, per member, the profile bound to the most assistants (ties: the
   default one, then the most recently updated) and rebinds every assistant of
   that member to it;
2. copies every other profile into ``runtime_profiles_archived`` before
   deleting it, so nothing a member typed is lost;
3. binds assistants that had no profile to their owner's profile;
4. adds ``agents.profile_revision_applied`` (the profile revision the pod was
   last started with) so the Portal can say "restart to apply";
5. makes ``runtime_profiles.owner_user_id`` unique.

Assistants that moved to another profile get ``profile_revision_applied = 0``:
their running pod still carries the old settings until it restarts, which the
Portal now shows instead of restarting them behind the member's back.
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "20260925_0036"
down_revision = "20260913_0035"
branch_labels = None
depends_on = None


ARCHIVE_TABLE = "runtime_profiles_archived"
OWNER_UNIQUE_INDEX = "uq_runtime_profiles_owner"


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


def _keeper(profiles: list, bound_counts: dict) -> object:
    def rank(profile):
        updated = profile.updated_at or profile.created_at or datetime.min
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
        return (bound_counts.get(profile.id, 0), bool(profile.is_default), updated)

    return max(profiles, key=rank)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column("agents", "profile_revision_applied"):
        op.add_column("agents", sa.Column("profile_revision_applied", sa.Integer(), nullable=True))

    if not _has_table(ARCHIVE_TABLE):
        op.create_table(
            ARCHIVE_TABLE,
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("merged_into_profile_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("archived_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if _has_table("runtime_profiles") and _has_table("agents"):
        profiles_table = sa.table(
            "runtime_profiles",
            sa.column("id", sa.String),
            sa.column("owner_user_id", sa.Integer),
            sa.column("name", sa.String),
            sa.column("description", sa.Text),
            sa.column("config_json", sa.Text),
            sa.column("revision", sa.Integer),
            sa.column("is_default", sa.Boolean),
            sa.column("created_at", sa.DateTime),
            sa.column("updated_at", sa.DateTime),
        )
        agents_table = sa.table(
            "agents",
            sa.column("id", sa.String),
            sa.column("owner_user_id", sa.Integer),
            sa.column("runtime_profile_id", sa.String),
            sa.column("profile_revision_applied", sa.Integer),
        )
        archive_table = sa.table(
            ARCHIVE_TABLE,
            sa.column("id", sa.String),
            sa.column("owner_user_id", sa.Integer),
            sa.column("name", sa.String),
            sa.column("description", sa.Text),
            sa.column("config_json", sa.Text),
            sa.column("revision", sa.Integer),
            sa.column("merged_into_profile_id", sa.String),
            sa.column("created_at", sa.DateTime),
            sa.column("archived_at", sa.DateTime),
        )

        profiles = list(bind.execute(sa.select(profiles_table)).fetchall())
        agents = list(bind.execute(sa.select(agents_table)).fetchall())
        bound_counts: dict = {}
        for agent in agents:
            if agent.runtime_profile_id:
                bound_counts[agent.runtime_profile_id] = bound_counts.get(agent.runtime_profile_id, 0) + 1

        by_owner: dict = {}
        for profile in profiles:
            by_owner.setdefault(profile.owner_user_id, []).append(profile)

        now = datetime.utcnow()
        keeper_by_owner: dict = {}
        for owner_user_id, owned in by_owner.items():
            keeper = _keeper(owned, bound_counts)
            keeper_by_owner[owner_user_id] = keeper
            bind.execute(
                profiles_table.update()
                .where(profiles_table.c.id == keeper.id)
                .values(is_default=True)
            )
            for profile in owned:
                if profile.id == keeper.id:
                    continue
                bind.execute(
                    archive_table.insert().values(
                        id=profile.id,
                        owner_user_id=profile.owner_user_id,
                        name=profile.name or "",
                        description=profile.description,
                        config_json=profile.config_json or "{}",
                        revision=profile.revision or 1,
                        merged_into_profile_id=keeper.id,
                        created_at=profile.created_at,
                        archived_at=now,
                    )
                )

        archived_ids = {
            profile.id
            for owner_user_id, owned in by_owner.items()
            for profile in owned
            if profile.id != keeper_by_owner[owner_user_id].id
        }
        for agent in agents:
            keeper = keeper_by_owner.get(agent.owner_user_id)
            if keeper is None:
                # No profile for this owner yet; Portal startup creates one and
                # binds the assistant (RuntimeProfileService.ensure_defaults_for_all_users).
                # Unbind it first when it points at a row that is about to go.
                if agent.runtime_profile_id in archived_ids:
                    bind.execute(
                        agents_table.update()
                        .where(agents_table.c.id == agent.id)
                        .values(runtime_profile_id=None, profile_revision_applied=0)
                    )
                continue
            if agent.runtime_profile_id == keeper.id:
                applied = keeper.revision or 1
            else:
                applied = 0
            bind.execute(
                agents_table.update()
                .where(agents_table.c.id == agent.id)
                .values(runtime_profile_id=keeper.id, profile_revision_applied=applied)
            )

        for owner_user_id, owned in by_owner.items():
            keeper = keeper_by_owner[owner_user_id]
            for profile in owned:
                if profile.id != keeper.id:
                    bind.execute(profiles_table.delete().where(profiles_table.c.id == profile.id))

    if _has_table("runtime_profiles") and not _has_index("runtime_profiles", OWNER_UNIQUE_INDEX):
        op.create_index(OWNER_UNIQUE_INDEX, "runtime_profiles", ["owner_user_id"], unique=True)


def downgrade() -> None:
    # Archived profiles stay archived: restoring them would need the old
    # agent bindings, which this revision does not keep.
    if _has_index("runtime_profiles", OWNER_UNIQUE_INDEX):
        op.drop_index(OWNER_UNIQUE_INDEX, table_name="runtime_profiles")
    if _has_column("agents", "profile_revision_applied"):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.drop_column("profile_revision_applied")
