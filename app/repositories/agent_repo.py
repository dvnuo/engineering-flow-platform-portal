from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent
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

    def get_by_id(self, agent_id: str) -> Optional[Agent]:
        return self.db.get(Agent, agent_id)

    def save(self, agent: Agent) -> Agent:
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        return agent

    def delete(self, agent: Agent) -> None:
        self.db.delete(agent)
        self.db.commit()
