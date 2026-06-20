from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.agent_task import AgentTask
from app.models.user import User
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

    def list_all(self, *, limit: int | None = None, offset: int = 0) -> list[AgentTask]:
        stmt = select(AgentTask).order_by(AgentTask.created_at.desc())
        if offset > 0:
            stmt = stmt.offset(offset)
        if limit is not None:
            if limit <= 0:
                return []
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def count_by_status(self) -> dict[str, int]:
        rows = self.db.execute(select(AgentTask.status, func.count()).group_by(AgentTask.status)).all()
        return {str(status or "unknown"): int(count or 0) for status, count in rows}

    def list_by_agent(self, agent_id: str) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(AgentTask.assignee_agent_id == agent_id)
            .order_by(AgentTask.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_root_task_id(self, root_task_id: str) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(or_(AgentTask.root_task_id == root_task_id, AgentTask.id == root_task_id))
            .order_by(AgentTask.created_at.asc(), AgentTask.id.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_visible_to_user(
        self,
        *,
        user_id: int,
        limit: int | None = None,
        offset: int = 0,
        status: str | None = None,
        owner: str | None = None,
        query: str | None = None,
    ) -> list[AgentTask]:
        filters = []
        normalized_status = (status or "").strip().lower()
        if normalized_status == "active":
            filters.append(AgentTask.status.in_(["queued", "running", "pending_restart"]))
        elif normalized_status == "attention":
            filters.append(AgentTask.status.in_(["failed", "blocked", "cancel_failed"]))
        elif normalized_status and normalized_status != "all":
            filters.append(AgentTask.status == normalized_status)

        if (owner or "").strip().lower() == "mine":
            filters.append(AgentTask.owner_user_id == user_id)

        normalized_query = (query or "").strip()
        if normalized_query:
            like_query = f"%{normalized_query}%"
            filters.append(
                or_(
                    AgentTask.id.ilike(like_query),
                    AgentTask.title.ilike(like_query),
                    AgentTask.summary.ilike(like_query),
                    AgentTask.error_message.ilike(like_query),
                    AgentTask.task_type.ilike(like_query),
                    AgentTask.skill_name.ilike(like_query),
                    AgentTask.source.ilike(like_query),
                    AgentTask.input_payload_json.ilike(like_query),
                )
            )

        stmt = (
            select(AgentTask)
            .order_by(AgentTask.updated_at.desc(), AgentTask.created_at.desc())
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        if offset > 0:
            stmt = stmt.offset(offset)
        if limit is not None:
            if limit <= 0:
                return []
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def list_visible_to_user_summaries(
        self,
        *,
        user_id: int,
        limit: int | None = None,
        offset: int = 0,
        status: str | None = None,
        owner: str | None = None,
        query: str | None = None,
    ) -> list[dict]:
        filters = []
        normalized_status = (status or "").strip().lower()
        if normalized_status == "active":
            filters.append(AgentTask.status.in_(["queued", "running", "pending_restart"]))
        elif normalized_status == "attention":
            filters.append(AgentTask.status.in_(["failed", "blocked", "cancel_failed"]))
        elif normalized_status and normalized_status != "all":
            filters.append(AgentTask.status == normalized_status)

        if (owner or "").strip().lower() == "mine":
            filters.append(AgentTask.owner_user_id == user_id)

        normalized_query = (query or "").strip()
        if normalized_query:
            like_query = f"%{normalized_query}%"
            filters.append(
                or_(
                    AgentTask.id.ilike(like_query),
                    AgentTask.title.ilike(like_query),
                    AgentTask.summary.ilike(like_query),
                    AgentTask.error_message.ilike(like_query),
                    AgentTask.task_type.ilike(like_query),
                    AgentTask.skill_name.ilike(like_query),
                    AgentTask.source.ilike(like_query),
                    AgentTask.input_payload_json.ilike(like_query),
                )
            )

        stmt = (
            select(
                AgentTask.id.label("id"),
                AgentTask.assignee_agent_id.label("assignee_agent_id"),
                AgentTask.source.label("source"),
                AgentTask.task_type.label("task_type"),
                AgentTask.title.label("title"),
                AgentTask.skill_name.label("skill_name"),
                AgentTask.status.label("status"),
                AgentTask.owner_user_id.label("owner_user_id"),
                AgentTask.created_by_user_id.label("created_by_user_id"),
                AgentTask.created_at.label("created_at"),
                AgentTask.updated_at.label("updated_at"),
                Agent.name.label("assignee_agent_name"),
                User.username.label("owner_username"),
                User.nickname.label("owner_nickname"),
            )
            .select_from(AgentTask)
            .outerjoin(Agent, Agent.id == AgentTask.assignee_agent_id)
            .outerjoin(User, User.id == AgentTask.owner_user_id)
            .order_by(AgentTask.updated_at.desc(), AgentTask.created_at.desc())
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        if offset > 0:
            stmt = stmt.offset(offset)
        if limit is not None:
            if limit <= 0:
                return []
            stmt = stmt.limit(limit)
        return [dict(row._mapping) for row in self.db.execute(stmt).all()]

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
                    AgentTask.dedupe_key == dedupe_hint,
                    AgentTask.input_payload_json == input_payload_json,
                    AgentTask.status.in_(["queued", "running", "done", "blocked"]),
                    AgentTask.created_at >= cutoff,
                )
            )
            .order_by(AgentTask.created_at.desc())
        )
        return self.db.scalars(stmt).first()

    def find_by_dedupe_key(
        self,
        *,
        assignee_agent_id: str,
        source: str,
        task_type: str,
        dedupe_key: str,
    ) -> Optional[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(
                and_(
                    AgentTask.assignee_agent_id == assignee_agent_id,
                    AgentTask.source == source,
                    AgentTask.task_type == task_type,
                    AgentTask.dedupe_key == dedupe_key,
                    AgentTask.status.in_(["queued", "running", "done", "blocked", "failed", "stale", "pending_restart"]),
                )
            )
            .order_by(AgentTask.created_at.desc())
        )
        return self.db.scalars(stmt).first()

    def claim_queued_for_dispatch(self, task_id: str, *, now: datetime | None = None) -> Optional[AgentTask]:
        now = now or datetime.utcnow()
        stmt = (
            update(AgentTask)
            .where(and_(AgentTask.id == task_id, AgentTask.status == "queued"))
            .values(status="running", started_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        result = self.db.execute(stmt)
        self.db.commit()
        if result.rowcount != 1:
            return None
        return self.get_by_id(task_id)

    def list_active_agent_async_tasks(self, *, limit: int | None = None) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(
                and_(
                    AgentTask.task_type == "agent_async_task",
                    AgentTask.status.in_(["queued", "running"]),
                )
            )
            .order_by(AgentTask.updated_at.asc(), AgentTask.created_at.asc(), AgentTask.id.asc())
        )
        if limit is not None:
            if limit <= 0:
                return []
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

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

    def list_active_tasks_for_delegation_item(
        self,
        *,
        assignee_agent_id: str,
        task_type: str,
        provider: str | None = None,
        trigger: str | None = None,
    ) -> list[AgentTask]:
        filters = [
            AgentTask.assignee_agent_id == assignee_agent_id,
            AgentTask.task_type == task_type,
            AgentTask.status.in_(["queued", "running"]),
        ]
        if provider:
            filters.append(AgentTask.provider == provider)
        if trigger:
            filters.append(AgentTask.trigger == trigger)
        stmt = (
            select(AgentTask)
            .where(and_(*filters))
            .order_by(AgentTask.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def delete(self, task: AgentTask) -> None:
        self.db.delete(task)
        self.db.commit()
