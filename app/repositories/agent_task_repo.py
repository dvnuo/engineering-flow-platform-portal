from sqlalchemy import or_, select
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

    def save(self, task: AgentTask) -> AgentTask:
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def delete(self, task: AgentTask) -> None:
        self.db.delete(task)
        self.db.commit()
