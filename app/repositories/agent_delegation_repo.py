from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_delegation import AgentDelegation


class AgentDelegationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AgentDelegation:
        delegation = AgentDelegation(**kwargs)
        self.db.add(delegation)
        self.db.commit()
        self.db.refresh(delegation)
        return delegation

    def get_by_id(self, delegation_id: str) -> AgentDelegation | None:
        return self.db.get(AgentDelegation, delegation_id)

    def list_by_group_id(self, group_id: str) -> list[AgentDelegation]:
        stmt = (
            select(AgentDelegation)
            .where(AgentDelegation.group_id == group_id)
            .order_by(AgentDelegation.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_leader_agent_id(self, leader_agent_id: str) -> list[AgentDelegation]:
        stmt = (
            select(AgentDelegation)
            .where(AgentDelegation.leader_agent_id == leader_agent_id)
            .order_by(AgentDelegation.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_assignee_agent_id(self, assignee_agent_id: str) -> list[AgentDelegation]:
        stmt = (
            select(AgentDelegation)
            .where(AgentDelegation.assignee_agent_id == assignee_agent_id)
            .order_by(AgentDelegation.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def save(self, delegation: AgentDelegation) -> AgentDelegation:
        self.db.add(delegation)
        self.db.commit()
        self.db.refresh(delegation)
        return delegation

    def find_by_agent_task_id(self, agent_task_id: str) -> AgentDelegation | None:
        stmt = select(AgentDelegation).where(AgentDelegation.agent_task_id == agent_task_id)
        return self.db.scalars(stmt).first()
