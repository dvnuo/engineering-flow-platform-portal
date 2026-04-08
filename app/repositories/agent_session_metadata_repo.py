from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_session_metadata import AgentSessionMetadata
from typing import Optional


class AgentSessionMetadataRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_agent_and_session(self, agent_id: str, session_id: str) -> Optional[AgentSessionMetadata]:
        stmt = select(AgentSessionMetadata).where(
            AgentSessionMetadata.agent_id == agent_id,
            AgentSessionMetadata.session_id == session_id,
        )
        return self.db.scalars(stmt).first()

    def list_by_agent(
        self,
        agent_id: str,
        *,
        group_id: str | None = None,
        latest_event_state: str | None = None,
        current_task_id: str | None = None,
    ) -> list[AgentSessionMetadata]:
        stmt = select(AgentSessionMetadata).where(AgentSessionMetadata.agent_id == agent_id)
        if group_id is not None:
            stmt = stmt.where(AgentSessionMetadata.group_id == group_id)
        if latest_event_state is not None:
            stmt = stmt.where(AgentSessionMetadata.latest_event_state == latest_event_state)
        if current_task_id is not None:
            stmt = stmt.where(AgentSessionMetadata.current_task_id == current_task_id)
        stmt = stmt.order_by(AgentSessionMetadata.updated_at.desc())
        return list(self.db.scalars(stmt).all())

    def upsert(self, *, agent_id: str, session_id: str, **fields) -> AgentSessionMetadata:
        record = self.get_by_agent_and_session(agent_id=agent_id, session_id=session_id)
        if record is None:
            record = AgentSessionMetadata(agent_id=agent_id, session_id=session_id)

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
