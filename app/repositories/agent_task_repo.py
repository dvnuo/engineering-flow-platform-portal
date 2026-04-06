from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.agent_task import AgentTask
from typing import Optional


class AgentTaskRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AgentTask:
        task = AgentTask(**kwargs)
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_by_id(self, task_id: str) -> Optional[AgentTask]:
        return self.db.get(AgentTask, task_id)

    def list_all(self) -> list[AgentTask]:
        return list(self.db.scalars(select(AgentTask).order_by(AgentTask.created_at.desc())).all())

    def list_by_agent(self, agent_id: str) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(or_(AgentTask.assignee_agent_id == agent_id, AgentTask.parent_agent_id == agent_id))
            .order_by(AgentTask.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def find_recent_duplicate(
        self,
        assignee_agent_id: str,
        source: str,
        task_type: str,
        dedupe_hint: str,
        input_payload_json: str | None,
        within_minutes: int = 60,
    ) -> Optional[AgentTask]:
        cutoff = datetime.utcnow() - timedelta(minutes=within_minutes)
        stmt = (
            select(AgentTask)
            .where(
                and_(
                    AgentTask.assignee_agent_id == assignee_agent_id,
                    AgentTask.source == source,
                    AgentTask.task_type == task_type,
                    AgentTask.shared_context_ref == dedupe_hint,
                    AgentTask.input_payload_json == input_payload_json,
                    AgentTask.created_at >= cutoff,
                )
            )
            .order_by(AgentTask.created_at.desc())
        )
        return self.db.scalars(stmt).first()

    def save(self, task: AgentTask) -> AgentTask:
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def delete(self, task: AgentTask) -> None:
        self.db.delete(task)
        self.db.commit()
