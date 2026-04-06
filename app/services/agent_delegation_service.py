import asyncio
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories.agent_delegation_repo import AgentDelegationRepository
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.schemas.agent_delegation import AgentDelegationCreateRequest
from app.services.task_dispatcher import TaskDispatcherService


@dataclass
class AgentDelegationServiceError(Exception):
    status_code: int
    detail: str


class AgentDelegationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.group_repo = AgentGroupRepository(db)
        self.member_repo = AgentGroupMemberRepository(db)
        self.agent_repo = AgentRepository(db)
        self.task_repo = AgentTaskRepository(db)
        self.delegation_repo = AgentDelegationRepository(db)
        self.dispatcher = TaskDispatcherService()

    @staticmethod
    def _parse_json_struct(raw: str | None, field_name: str):
        if raw is None or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentDelegationServiceError(status_code=400, detail=f"{field_name} must be valid JSON") from exc
        if not isinstance(parsed, (dict, list)):
            raise AgentDelegationServiceError(status_code=422, detail=f"{field_name} must decode to a JSON object or array")
        return parsed

    def create_delegation(self, payload: AgentDelegationCreateRequest):
        group = self.group_repo.get_by_id(payload.group_id)
        if not group:
            raise AgentDelegationServiceError(status_code=404, detail="Group not found")

        if payload.leader_agent_id != group.leader_agent_id:
            raise AgentDelegationServiceError(status_code=403, detail="leader_agent_id must match group leader")

        leader_member = self.member_repo.get_by_group_and_agent(group.id, payload.leader_agent_id)
        if not leader_member or leader_member.role != "leader":
            raise AgentDelegationServiceError(status_code=403, detail="Leader agent must be a leader member of the group")

        assignee_member = self.member_repo.get_by_group_and_agent(group.id, payload.assignee_agent_id)
        if not assignee_member:
            raise AgentDelegationServiceError(status_code=403, detail="Assignee agent must be a member of the group")

        if payload.visibility not in {"leader_only", "group_visible"}:
            raise AgentDelegationServiceError(status_code=422, detail="Invalid visibility")

        if payload.parent_agent_id and payload.parent_agent_id == payload.assignee_agent_id and payload.leader_agent_id == payload.assignee_agent_id:
            raise AgentDelegationServiceError(status_code=409, detail="Delegation loop is not allowed")

        if not self.agent_repo.get_by_id(payload.assignee_agent_id):
            raise AgentDelegationServiceError(status_code=404, detail="Assignee agent not found")
        if payload.parent_agent_id and not self.agent_repo.get_by_id(payload.parent_agent_id):
            raise AgentDelegationServiceError(status_code=404, detail="Parent agent not found")

        input_artifacts = self._parse_json_struct(payload.input_artifacts_json, "input_artifacts_json")
        expected_output_schema = self._parse_json_struct(payload.expected_output_schema_json, "expected_output_schema_json")
        retry_policy = self._parse_json_struct(payload.retry_policy_json, "retry_policy_json")
        skill_kwargs = self._parse_json_struct(payload.skill_kwargs_json, "skill_kwargs_json")

        ephemeral_policy = self._parse_json_struct(group.ephemeral_agent_policy_json, "ephemeral_agent_policy_json")

        audit_trace = {
            "skill_name": payload.skill_name,
            "skill_kwargs": skill_kwargs,
            "ephemeral_agent_policy": ephemeral_policy,
        }
        if isinstance(skill_kwargs, dict) and skill_kwargs.get("agent_mode") == "task":
            audit_trace["agent_mode"] = "task"
            audit_trace["ephemeral_task_agent_intent"] = True

        delegation = self.delegation_repo.create(
            group_id=payload.group_id,
            parent_agent_id=payload.parent_agent_id,
            leader_agent_id=payload.leader_agent_id,
            assignee_agent_id=payload.assignee_agent_id,
            agent_task_id=None,
            objective=payload.objective,
            scoped_context_ref=payload.scoped_context_ref,
            input_artifacts_json=payload.input_artifacts_json,
            expected_output_schema_json=payload.expected_output_schema_json,
            deadline_at=payload.deadline_at,
            retry_policy_json=payload.retry_policy_json,
            visibility=payload.visibility,
            status="queued",
            audit_trace_json=json.dumps(audit_trace),
        )

        task_input_payload = {
            "delegation_id": delegation.id,
            "group_id": payload.group_id,
            "parent_agent_id": payload.parent_agent_id or payload.leader_agent_id,
            "leader_agent_id": payload.leader_agent_id,
            "assignee_agent_id": payload.assignee_agent_id,
            "objective": payload.objective,
            "scoped_context_ref": payload.scoped_context_ref,
            "input_artifacts": input_artifacts,
            "expected_output_schema": expected_output_schema,
            "deadline": payload.deadline_at.isoformat() if payload.deadline_at else None,
            "retry_policy": retry_policy,
            "visibility": payload.visibility,
            "skill_name": payload.skill_name,
            "skill_kwargs": skill_kwargs,
        }

        task = self.task_repo.create(
            task_type="delegation_task",
            source="agent",
            group_id=payload.group_id,
            parent_agent_id=payload.parent_agent_id or payload.leader_agent_id,
            assignee_agent_id=payload.assignee_agent_id,
            shared_context_ref=payload.scoped_context_ref,
            input_payload_json=json.dumps(task_input_payload),
            status="queued",
            result_payload_json=None,
            retry_count=0,
        )

        delegation.agent_task_id = task.id
        self.delegation_repo.save(delegation)

        dispatch_result = asyncio.run(self.dispatcher.dispatch_task(task.id, self.db))
        if not dispatch_result.dispatched:
            raise AgentDelegationServiceError(status_code=409, detail=f"Delegation task dispatch failed: {dispatch_result.message}")

        updated_delegation = self.delegation_repo.get_by_id(delegation.id)
        return updated_delegation or delegation

    def get_delegation(self, delegation_id: str):
        delegation = self.delegation_repo.get_by_id(delegation_id)
        if not delegation:
            raise AgentDelegationServiceError(status_code=404, detail="Delegation not found")
        return delegation

    def list_group_delegations(self, group_id: str):
        group = self.group_repo.get_by_id(group_id)
        if not group:
            raise AgentDelegationServiceError(status_code=404, detail="Group not found")
        return self.delegation_repo.list_by_group_id(group_id)
