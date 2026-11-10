from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.group import Group
from app.models.user import User

DEFAULT_GROUP_NAME = "Default"


class GroupRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, group_id: int | None) -> Optional[Group]:
        if group_id is None:
            return None
        return self.db.get(Group, group_id)

    def get_by_name(self, name: str) -> Optional[Group]:
        cleaned = str(name or "").strip().lower()
        if not cleaned:
            return None
        return self.db.scalar(select(Group).where(func.lower(Group.name) == cleaned))

    def get_default(self) -> Optional[Group]:
        return self.db.scalar(select(Group).where(Group.is_default.is_(True)).order_by(Group.id.asc()).limit(1))

    def ensure_default(self) -> Group:
        """The default group, created when a database has none yet (a test database, say)."""

        group = self.get_default()
        if group is not None:
            return group
        try:
            group = Group(name=DEFAULT_GROUP_NAME, is_default=True, config_json="{}", connector_modes_json="{}")
            self.db.add(group)
            self.db.commit()
            self.db.refresh(group)
            return group
        except IntegrityError:
            # Another request created it first, or a group already took the name.
            self.db.rollback()
            group = self.get_default()
            if group is not None:
                return group
            named = self.get_by_name(DEFAULT_GROUP_NAME)
            if named is None:
                raise
            named.is_default = True
            return self.save(named)

    def list_all(self) -> list[Group]:
        """The default group first, then by name."""

        return list(self.db.scalars(select(Group).order_by(Group.is_default.desc(), func.lower(Group.name).asc())).all())

    def create(self, name: str, *, description: str | None = None, updated_by_user_id: int | None = None) -> Group:
        group = Group(
            name=name,
            description=description,
            is_default=False,
            config_json="{}",
            connector_modes_json="{}",
            updated_by_user_id=updated_by_user_id,
        )
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    def save(self, group: Group) -> Group:
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    def delete(self, group: Group) -> None:
        self.db.delete(group)
        self.db.commit()

    def member_ids(self, group: Group) -> list[int]:
        """Ids of the accounts in ``group``; for the default group, those with no group too."""

        condition = User.group_id == group.id
        if group.is_default:
            condition = condition | User.group_id.is_(None)
        return list(self.db.scalars(select(User.id).where(condition).order_by(User.id.asc())).all())

    def member_counts(self) -> dict[int, int]:
        """``{group id: accounts}``; accounts with no group count towards the default group."""

        default = self.get_default()
        counts: dict[int, int] = {}
        for group_id, count in self.db.execute(select(User.group_id, func.count(User.id)).group_by(User.group_id)).all():
            key = group_id if group_id is not None else (default.id if default else None)
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + int(count)
        return counts
