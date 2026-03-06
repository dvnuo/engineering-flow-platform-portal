import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from typing import Optional


class AuditRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, action: str, target_type: str, target_id: str, user_id: Optional[int] = None, details: Optional[dict] = None) -> AuditLog:
        row = AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details_json=json.dumps(details) if details else None,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_all(self) -> list[AuditLog]:
        return list(self.db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc())).all())
