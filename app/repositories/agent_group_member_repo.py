from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.agent_group_member import AgentGroupMember


class AgentGroupMemberRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> AgentGroupMember:
        member = self.create_no_commit(**kwargs)
        self.db.commit()
        self.db.refresh(member)
        return member

    def create_no_commit(self, **kwargs) -> AgentGroupMember:
        member = AgentGroupMember(**kwargs)
        self.db.add(member)
        self.db.flush()
        return member

    def get_by_id(self, member_id: str) -> AgentGroupMember | None:
        return self.db.get(AgentGroupMember, member_id)

    def list_by_group(self, group_id: str) -> list[AgentGroupMember]:
        stmt = (
            select(AgentGroupMember)
            .where(AgentGroupMember.group_id == group_id)
            .order_by(AgentGroupMember.created_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_group_leader_member(self, group_id: str) -> AgentGroupMember | None:
        stmt = select(AgentGroupMember).where(
            and_(
                AgentGroupMember.group_id == group_id,
                AgentGroupMember.role == "leader",
            )
        )
        return self.db.scalars(stmt).first()

    def get_by_group_and_agent(self, group_id: str, agent_id: str) -> AgentGroupMember | None:
        stmt = select(AgentGroupMember).where(
            and_(
                AgentGroupMember.group_id == group_id,
                AgentGroupMember.agent_id == agent_id,
            )
        )
        return self.db.scalars(stmt).first()

    def get_by_group_and_user(self, group_id: str, user_id: int) -> AgentGroupMember | None:
        stmt = select(AgentGroupMember).where(
            and_(
                AgentGroupMember.group_id == group_id,
                AgentGroupMember.user_id == user_id,
            )
        )
        return self.db.scalars(stmt).first()

    def delete(self, member: AgentGroupMember) -> None:
        self.db.delete(member)
        self.db.commit()
