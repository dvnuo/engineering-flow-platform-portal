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

    def list_visible_to_user(self, *, user_id: int, visible_group_ids: list[str] | None = None) -> list[AgentTask]:
        filters = [AgentTask.owner_user_id == user_id, AgentTask.created_by_user_id == user_id]
        group_ids = [group_id for group_id in (visible_group_ids or []) if group_id]
        if group_ids:
            filters.append(AgentTask.group_id.in_(group_ids))
        stmt = (
            select(AgentTask)
            .where(or_(*filters))
            .order_by(AgentTask.updated_at.desc(), AgentTask.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_group_id(self, group_id: str) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(AgentTask.group_id == group_id)
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
                    or_(
                        AgentTask.dedupe_key == dedupe_hint,
                        and_(AgentTask.dedupe_key.is_(None), AgentTask.shared_context_ref == dedupe_hint),
                    ),
                    AgentTask.input_payload_json == input_payload_json,
                    AgentTask.status.in_(["queued", "running", "done", "blocked"]),
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

    def save_all(self, tasks: list[AgentTask]) -> None:
        if not tasks:
            return
        for task in tasks:
            self.db.add(task)
        self.db.commit()

    def list_active_github_review_tasks_for_family(
        self,
        *,
        assignee_agent_id: str,
        family_prefix: str,
    ) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(
                and_(
                    AgentTask.assignee_agent_id == assignee_agent_id,
                    AgentTask.source == "github",
                    AgentTask.task_type == "github_review_task",
                    AgentTask.status.in_(["queued", "running"]),
                    AgentTask.shared_context_ref.like(f"{family_prefix}%"),
                )
            )
            .order_by(AgentTask.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_active_tasks_for_bundle(
        self,
        *,
        assignee_agent_id: str,
        bundle_id: str,
        task_type: str | None = None,
    ) -> list[AgentTask]:
        filters = [
            AgentTask.assignee_agent_id == assignee_agent_id,
            AgentTask.bundle_id == bundle_id,
            AgentTask.status.in_(["queued", "running"]),
        ]
        if task_type:
            filters.append(AgentTask.task_type == task_type)
        stmt = select(AgentTask).where(and_(*filters)).order_by(AgentTask.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def delete(self, task: AgentTask) -> None:
        self.db.delete(task)
        self.db.commit()
