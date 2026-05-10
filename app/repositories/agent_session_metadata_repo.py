from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent_session_metadata import AgentSessionMetadata
from typing import Optional


class AgentSessionMetadataRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_agent_and_session(self, agent_id: str, session_id: str, *, include_deleted: bool = False) -> Optional[AgentSessionMetadata]:
        stmt = select(AgentSessionMetadata).where(
            AgentSessionMetadata.agent_id == agent_id,
            AgentSessionMetadata.session_id == session_id,
        )
        if not include_deleted:
            stmt = stmt.where(AgentSessionMetadata.deleted_at.is_(None))
        return self.db.scalars(stmt).first()

    def list_by_agent(
        self,
        agent_id: str,
        *,
        group_id: str | None = None,
        latest_event_state: str | None = None,
        current_task_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[AgentSessionMetadata]:
        stmt = select(AgentSessionMetadata).where(AgentSessionMetadata.agent_id == agent_id)
        if not include_deleted:
            stmt = stmt.where(AgentSessionMetadata.deleted_at.is_(None))
        if group_id is not None:
            stmt = stmt.where(AgentSessionMetadata.group_id == group_id)
        if latest_event_state is not None:
            stmt = stmt.where(AgentSessionMetadata.latest_event_state == latest_event_state)
        if current_task_id is not None:
            stmt = stmt.where(AgentSessionMetadata.current_task_id == current_task_id)
        stmt = stmt.order_by(AgentSessionMetadata.updated_at.desc())
        return list(self.db.scalars(stmt).all())

    def list_by_agent_and_session_ids(self, agent_id: str, session_ids: list[str], *, include_deleted: bool = False) -> list[AgentSessionMetadata]:
        if not session_ids:
            return []
        stmt = select(AgentSessionMetadata).where(
            AgentSessionMetadata.agent_id == agent_id,
            AgentSessionMetadata.session_id.in_(session_ids),
        )
        if not include_deleted:
            stmt = stmt.where(AgentSessionMetadata.deleted_at.is_(None))
        return list(self.db.scalars(stmt).all())

    def upsert(self, *, agent_id: str, session_id: str, allow_reactivate: bool = False, **fields) -> AgentSessionMetadata:
        record = self.get_by_agent_and_session(agent_id=agent_id, session_id=session_id, include_deleted=True)
        is_new = record is None
        if is_new:
            record = AgentSessionMetadata(agent_id=agent_id, session_id=session_id)

        for key, value in fields.items():
            if hasattr(record, key):
                setattr(record, key, value)
        if getattr(record, 'deleted_at', None) is not None and not allow_reactivate:
            record.deleted_at = record.deleted_at
        record.agent_id = agent_id
        record.session_id = session_id
        record.updated_at = datetime.utcnow()

        self.db.add(record)
        try:
            self.db.commit()
        except IntegrityError:
            if not is_new:
                raise
            self.db.rollback()
            record = self.get_by_agent_and_session(agent_id=agent_id, session_id=session_id, include_deleted=True)
            if record is None:
                raise
            for key, value in fields.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            record.agent_id = agent_id
            record.session_id = session_id
            record.updated_at = datetime.utcnow()
            self.db.add(record)
            self.db.commit()
        self.db.refresh(record)
        return record

    def mark_deleted(self, agent_id: str, session_id: str) -> tuple[AgentSessionMetadata, bool]:
        now = datetime.utcnow()
        record = self.get_by_agent_and_session(agent_id=agent_id, session_id=session_id, include_deleted=True)
        already_deleted = False
        if record is None:
            record = AgentSessionMetadata(
                agent_id=agent_id,
                session_id=session_id,
                latest_event_type='session.deleted',
                latest_event_state='deleted',
                created_at=now,
                updated_at=now,
                deleted_at=now,
            )
        elif record.deleted_at is not None:
            already_deleted = True
        else:
            record.deleted_at = now
            record.updated_at = now
            if not record.latest_event_type:
                record.latest_event_type = 'session.deleted'
            record.latest_event_state = 'deleted'
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record, already_deleted
