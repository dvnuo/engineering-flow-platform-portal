from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_delegation_repo import AgentDelegationRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.user_repo import UserRepository
from app.schemas.agent_group import (
    AgentGroupCreateRequest,
    AgentGroupMemberCreateRequest,
    AgentGroupTaskCreateRequest,
    AgentGroupTaskSummaryResponse,
)


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
        self.task_repo = AgentTaskRepository(db)
        self.delegation_repo = AgentDelegationRepository(db)
        self.user_repo = UserRepository(db)

    def _get_group_or_raise(self, group_id: str):
        group = self.group_repo.get_by_id(group_id)
        if not group:
            raise AgentGroupServiceError(status_code=404, detail="Group not found")
        return group

    def _get_agent_or_raise(self, agent_id: str, detail: str):
        agent = self.agent_repo.get_by_id(agent_id)
        if not agent:
            raise AgentGroupServiceError(status_code=404, detail=detail)
        return agent

    def _get_user_or_raise(self, user_id: int, detail: str):
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise AgentGroupServiceError(status_code=404, detail=detail)
        return user

    def create_group_with_members(self, payload: AgentGroupCreateRequest, created_by_user_id: int):
        leader_agent = self._get_agent_or_raise(payload.leader_agent_id, "Leader agent not found")

        unique_user_ids = list(dict.fromkeys(payload.member_user_ids))
        unique_agent_ids = list(dict.fromkeys(payload.member_agent_ids))

        for member_user_id in unique_user_ids:
            self._get_user_or_raise(member_user_id, f"User member not found: {member_user_id}")

        for member_agent_id in unique_agent_ids:
            self._get_agent_or_raise(member_agent_id, f"Agent member not found: {member_agent_id}")

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
        group = self._get_group_or_raise(group_id)

        member_type = (payload.member_type or "").strip().lower()
        if member_type not in {"user", "agent"}:
            raise AgentGroupServiceError(status_code=400, detail="member_type must be 'user' or 'agent'")

        if payload.role == "leader":
            raise AgentGroupServiceError(status_code=409, detail="Group already has a leader member")

        if member_type == "user":
            if not payload.user_id or payload.agent_id is not None:
                raise AgentGroupServiceError(status_code=400, detail="user member must set user_id and omit agent_id")
            self._get_user_or_raise(payload.user_id, "User member not found")
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
        self._get_agent_or_raise(payload.agent_id, "Agent member not found")
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
        group = self._get_group_or_raise(group_id)

        member = self.member_repo.get_by_id(member_id)
        if not member or member.group_id != group_id:
            raise AgentGroupServiceError(status_code=404, detail="Group member not found")

        if member.role == "leader" and member.agent_id == group.leader_agent_id:
            raise AgentGroupServiceError(status_code=409, detail="Cannot remove current group leader member")

        self.member_repo.delete(member)

    def list_group_tasks(self, group_id: str):
        _group = self._get_group_or_raise(group_id)
        return self.task_repo.list_by_group_id(group_id)

    def get_group_task_summary(self, group_id: str) -> AgentGroupTaskSummaryResponse:
        tasks = self.list_group_tasks(group_id)
        counts = {
            "queued": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
        }
        for task in tasks:
            if task.status in counts:
                counts[task.status] += 1
        return AgentGroupTaskSummaryResponse(
            group_id=group_id,
            total=len(tasks),
            queued=counts["queued"],
            running=counts["running"],
            done=counts["done"],
            failed=counts["failed"],
        )

    def create_group_task(self, group_id: str, payload: AgentGroupTaskCreateRequest):
        _group = self._get_group_or_raise(group_id)
        _assignee_agent = self._get_agent_or_raise(payload.assignee_agent_id, "Assignee agent not found")

        if payload.parent_agent_id is not None:
            self._get_agent_or_raise(payload.parent_agent_id, "Parent agent not found")

        return self.task_repo.create(
            group_id=group_id,
            parent_agent_id=payload.parent_agent_id,
            assignee_agent_id=payload.assignee_agent_id,
            source=payload.source,
            task_type=payload.task_type,
            input_payload_json=payload.input_payload_json,
            shared_context_ref=payload.shared_context_ref,
            status=payload.status,
            result_payload_json=payload.result_payload_json,
            retry_count=payload.retry_count,
        )

    def list_group_delegations(self, group_id: str):
        _group = self._get_group_or_raise(group_id)
        return self.delegation_repo.list_by_group_id(group_id)

    def get_group_task_board(self, group_id: str) -> dict:
        group = self._get_group_or_raise(group_id)
        delegations = self.list_group_delegations(group_id)
        counts = {
            "queued": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
        }
        for delegation in delegations:
            if delegation.status in counts:
                counts[delegation.status] += 1

        return {
            "group_id": group_id,
            "leader_agent_id": group.leader_agent_id,
            "summary": {
                "total": len(delegations),
                "queued": counts["queued"],
                "running": counts["running"],
                "done": counts["done"],
                "failed": counts["failed"],
            },
            "items": delegations,
        }
