from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.user_repo import UserRepository
from app.schemas.agent_group import AgentGroupCreateRequest, AgentGroupMemberCreateRequest


@dataclass
class AgentGroupServiceError(Exception):
    status_code: int
    detail: str


class AgentGroupService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.group_repo = AgentGroupRepository(db)
        self.member_repo = AgentGroupMemberRepository(db)
        self.agent_repo = AgentRepository(db)
        self.user_repo = UserRepository(db)

    def create_group_with_members(self, payload: AgentGroupCreateRequest, created_by_user_id: int):
        leader_agent = self.agent_repo.get_by_id(payload.leader_agent_id)
        if not leader_agent:
            raise AgentGroupServiceError(status_code=404, detail="Leader agent not found")

        unique_user_ids = list(dict.fromkeys(payload.member_user_ids))
        unique_agent_ids = list(dict.fromkeys(payload.member_agent_ids))

        for member_user_id in unique_user_ids:
            if not self.user_repo.get_by_id(member_user_id):
                raise AgentGroupServiceError(status_code=404, detail=f"User member not found: {member_user_id}")

        for member_agent_id in unique_agent_ids:
            if not self.agent_repo.get_by_id(member_agent_id):
                raise AgentGroupServiceError(status_code=404, detail=f"Agent member not found: {member_agent_id}")

        try:
            group = self.group_repo.create_no_commit(
                name=payload.name,
                leader_agent_id=payload.leader_agent_id,
                shared_context_policy_json=payload.shared_context_policy_json,
                task_routing_policy_json=payload.task_routing_policy_json,
                ephemeral_agent_policy_json=payload.ephemeral_agent_policy_json,
                created_by_user_id=created_by_user_id,
            )

            self.member_repo.create_no_commit(
                group_id=group.id,
                member_type="agent",
                agent_id=payload.leader_agent_id,
                user_id=None,
                role="leader",
            )

            for member_agent_id in unique_agent_ids:
                if member_agent_id == payload.leader_agent_id:
                    continue
                if self.member_repo.get_by_group_and_agent(group.id, member_agent_id):
                    continue
                self.member_repo.create_no_commit(
                    group_id=group.id,
                    member_type="agent",
                    agent_id=member_agent_id,
                    user_id=None,
                    role="member",
                )

            for member_user_id in unique_user_ids:
                if self.member_repo.get_by_group_and_user(group.id, member_user_id):
                    continue
                self.member_repo.create_no_commit(
                    group_id=group.id,
                    member_type="user",
                    user_id=member_user_id,
                    agent_id=None,
                    role="member",
                )
            self.db.commit()
        except AgentGroupServiceError:
            raise
        except Exception as exc:
            self.db.rollback()
            raise AgentGroupServiceError(status_code=400, detail=f"Failed to create group: {exc}") from exc

        self.db.refresh(group)
        members = self.member_repo.list_by_group(group.id)
        return group, members

    def add_group_member(self, group_id: str, payload: AgentGroupMemberCreateRequest):
        group = self.group_repo.get_by_id(group_id)
        if not group:
            raise AgentGroupServiceError(status_code=404, detail="Group not found")

        member_type = (payload.member_type or "").strip().lower()
        if member_type not in {"user", "agent"}:
            raise AgentGroupServiceError(status_code=400, detail="member_type must be 'user' or 'agent'")

        if payload.role == "leader":
            raise AgentGroupServiceError(status_code=409, detail="Group already has a leader member")

        if member_type == "user":
            if not payload.user_id or payload.agent_id is not None:
                raise AgentGroupServiceError(status_code=400, detail="user member must set user_id and omit agent_id")
            if not self.user_repo.get_by_id(payload.user_id):
                raise AgentGroupServiceError(status_code=404, detail="User member not found")
            existing = self.member_repo.get_by_group_and_user(group.id, payload.user_id)
            if existing:
                return existing
            member = self.member_repo.create(
                group_id=group.id,
                member_type="user",
                user_id=payload.user_id,
                agent_id=None,
                role=payload.role,
            )
            return member

        if not payload.agent_id or payload.user_id is not None:
            raise AgentGroupServiceError(status_code=400, detail="agent member must set agent_id and omit user_id")
        if not self.agent_repo.get_by_id(payload.agent_id):
            raise AgentGroupServiceError(status_code=404, detail="Agent member not found")
        existing = self.member_repo.get_by_group_and_agent(group.id, payload.agent_id)
        if existing:
            return existing

        member = self.member_repo.create(
            group_id=group.id,
            member_type="agent",
            user_id=None,
            agent_id=payload.agent_id,
            role=payload.role,
        )
        return member

    def remove_group_member(self, group_id: str, member_id: str) -> None:
        group = self.group_repo.get_by_id(group_id)
        if not group:
            raise AgentGroupServiceError(status_code=404, detail="Group not found")

        member = self.member_repo.get_by_id(member_id)
        if not member or member.group_id != group_id:
            raise AgentGroupServiceError(status_code=404, detail="Group member not found")

        if member.role == "leader" and member.agent_id == group.leader_agent_id:
            raise AgentGroupServiceError(status_code=409, detail="Cannot remove current group leader member")

        self.member_repo.delete(member)
