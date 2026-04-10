from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_group import AgentGroup


class AgentGroupRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AgentGroup:
        group = self.create_no_commit(**kwargs)
        self.db.commit()
        self.db.refresh(group)
        return group

    def create_no_commit(self, **kwargs) -> AgentGroup:
        group = AgentGroup(**kwargs)
        self.db.add(group)
        self.db.flush()
        return group

    def get_by_id(self, group_id: str) -> AgentGroup | None:
        return self.db.get(AgentGroup, group_id)

    def list_all(self) -> list[AgentGroup]:
        stmt = select(AgentGroup).order_by(AgentGroup.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def save(self, group: AgentGroup) -> AgentGroup:
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group

    def delete(self, group: AgentGroup) -> None:
        self.db.delete(group)
        self.db.commit()
