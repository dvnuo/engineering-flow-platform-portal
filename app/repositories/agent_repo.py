from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.runtime_profile_sync_job import RuntimeProfileSyncJob
from typing import Optional


class AgentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> Agent:
        agent = Agent(**kwargs)
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def list_by_owner(self, owner_user_id: int) -> list[Agent]:
        return list(
            self.db.scalars(select(Agent).where(Agent.owner_user_id == owner_user_id).order_by(Agent.created_at.desc())).all()
        )

    def list_public(self) -> list[Agent]:
        return list(
            self.db.scalars(select(Agent).where(Agent.visibility == "public").order_by(Agent.created_at.desc())).all()
        )

    def list_all(self) -> list[Agent]:
        return list(self.db.scalars(select(Agent).order_by(Agent.created_at.desc())).all())

    def list_by_status(self, status: str, *, limit: Optional[int] = None) -> list[Agent]:
        stmt = select(Agent).where(Agent.status == status).order_by(Agent.created_at.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_by_id(self, agent_id: str) -> Optional[Agent]:
        return self.db.get(Agent, agent_id)

    def save(self, agent: Agent) -> Agent:
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def delete(self, agent: Agent) -> None:
        # Runtime profile sync jobs are durable queue entries, not audit records.
        # Once an agent is deleted, queued/apply jobs for that agent can never
        # succeed and must not block the agent deletion through the FK to agents.id.
        self.db.query(RuntimeProfileSyncJob).filter(
            RuntimeProfileSyncJob.agent_id == agent.id
        ).delete(synchronize_session=False)
        self.db.delete(agent)
        self.db.commit()
