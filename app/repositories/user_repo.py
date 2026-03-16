from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from typing import Optional


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_username(self, username: str) -> Optional[User]:
        return self.db.scalar(select(User).where(User.username == username))

    def get_by_id(self, user_id: int) -> Optional[User]:
        return self.db.get(User, user_id)

    def list_all(self) -> list[User]:
        return list(self.db.scalars(select(User).order_by(User.id.asc())).all())

    def create(self, username: str, password_hash: str, role: str = "user", nickname: Optional[str] = None) -> User:
        user = User(username=username, password_hash=password_hash, role=role, nickname=nickname)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_password(self, user: User, password_hash: str) -> User:
        user.password_hash = password_hash
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
