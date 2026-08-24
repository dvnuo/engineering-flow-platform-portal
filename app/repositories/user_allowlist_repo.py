from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user_allowlist import UserAllowlistEntry


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


class UserAllowlistRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, entry_id: int) -> Optional[UserAllowlistEntry]:
        return self.db.get(UserAllowlistEntry, entry_id)

    def get_by_username(self, username: str) -> Optional[UserAllowlistEntry]:
        normalized = normalize_username(username)
        if not normalized:
            return None
        return self.db.scalar(
            select(UserAllowlistEntry).where(func.lower(UserAllowlistEntry.username) == normalized)
        )

    def get_active_by_username(self, username: str) -> Optional[UserAllowlistEntry]:
        entry = self.get_by_username(username)
        return entry if entry and entry.is_active else None

    def list_all(self) -> list[UserAllowlistEntry]:
        return list(
            self.db.scalars(
                select(UserAllowlistEntry).order_by(
                    UserAllowlistEntry.is_active.desc(),
                    UserAllowlistEntry.username.asc(),
                )
            ).all()
        )

    def create(
        self,
        username: str,
        *,
        role: str = "user",
        added_by_user_id: Optional[int] = None,
    ) -> UserAllowlistEntry:
        entry = UserAllowlistEntry(
            username=normalize_username(username),
            role=role,
            is_active=True,
            added_by_user_id=added_by_user_id,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def ensure(
        self,
        username: str,
        *,
        role: str = "user",
        added_by_user_id: Optional[int] = None,
        reactivate: bool = False,
        update_role: bool = True,
    ) -> UserAllowlistEntry:
        entry = self.get_by_username(username)
        if entry:
            changed = False
            if reactivate and not entry.is_active:
                entry.is_active = True
                changed = True
            if update_role and entry.role != role:
                entry.role = role
                changed = True
            if changed:
                self.db.add(entry)
                self.db.commit()
                self.db.refresh(entry)
            return entry
        return self.create(username, role=role, added_by_user_id=added_by_user_id)

    def update_role(self, entry: UserAllowlistEntry, role: str) -> UserAllowlistEntry:
        entry.role = role
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def delete(self, entry: UserAllowlistEntry) -> None:
        self.db.delete(entry)
        self.db.commit()
