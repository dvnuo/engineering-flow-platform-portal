from datetime import datetime
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.user_connector import UserConnector


class UserConnectorRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, owner_user_id: int, connector_type: str) -> UserConnector | None:
        return self.db.scalar(
            select(UserConnector).where(
                and_(
                    UserConnector.owner_user_id == owner_user_id,
                    UserConnector.connector_type == connector_type,
                )
            )
        )

    def list_by_owner(self, owner_user_id: int) -> list[UserConnector]:
        query = (
            select(UserConnector)
            .where(UserConnector.owner_user_id == owner_user_id)
            .order_by(UserConnector.connector_type.asc())
        )
        return list(self.db.scalars(query).all())

    def list_enabled_by_owner(self, owner_user_id: int) -> list[UserConnector]:
        query = select(UserConnector).where(
            and_(UserConnector.owner_user_id == owner_user_id, UserConnector.enabled.is_(True))
        )
        return list(self.db.scalars(query).all())

    def upsert(self, owner_user_id: int, connector_type: str, *, enabled: bool, config_json: str) -> UserConnector:
        row = self.get(owner_user_id, connector_type)
        if row is None:
            row = UserConnector(owner_user_id=owner_user_id, connector_type=connector_type)
            self.db.add(row)
        row.enabled = bool(enabled)
        row.config_json = config_json
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return row

    def mark_verified(
        self,
        owner_user_id: int,
        connector_type: str,
        *,
        ok: bool,
        verification_json: Optional[str] = None,
    ) -> UserConnector:
        row = self.get(owner_user_id, connector_type)
        if row is None:
            row = UserConnector(owner_user_id=owner_user_id, connector_type=connector_type, enabled=False, config_json="{}")
            self.db.add(row)
        if ok:
            row.last_verified_at = datetime.utcnow()
        row.verification_json = verification_json
        row.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(row)
        return row
