import json
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

from app.repositories.agent_delegation_repo import AgentDelegationRepository
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.group_shared_context_snapshot_repo import GroupSharedContextSnapshotRepository
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
        self.context_snapshot_repo = GroupSharedContextSnapshotRepository(db)
        self.dispatcher = TaskDispatcherService()

    @staticmethod
    def _parse_json_object(raw: str | None, field_name: str, default_value: dict | None = None) -> dict:
        if raw is None or not raw.strip():
            return dict(default_value or {})
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentDelegationServiceError(status_code=400, detail=f"{field_name} must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise AgentDelegationServiceError(status_code=422, detail=f"{field_name} must decode to a JSON object")
        return parsed

    @staticmethod
    def _parse_optional_json_object(raw: str | None, field_name: str) -> dict | None:
        if raw is None or not raw.strip():
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentDelegationServiceError(status_code=400, detail=f"{field_name} must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise AgentDelegationServiceError(status_code=422, detail=f"{field_name} must decode to a JSON object")
        return parsed

    @staticmethod
    def _parse_json_array(raw: str | None, field_name: str) -> list:
        if raw is None or not raw.strip():
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentDelegationServiceError(status_code=400, detail=f"{field_name} must be valid JSON") from exc
        if not isinstance(parsed, list):
            raise AgentDelegationServiceError(status_code=422, detail=f"{field_name} must decode to a JSON array")
        return parsed

    def _is_leader_owner(self, group, user) -> bool:
        leader = self.agent_repo.get_by_id(group.leader_agent_id)
        if not leader:
            return False
        return leader.owner_user_id == getattr(user, "id", None)

    def is_group_participant(self, group_id: str, user) -> bool:
        members = self.member_repo.list_by_group(group_id)
        user_id = getattr(user, "id", None)
        if user_id is None:
            return False

        for member in members:
            if member.user_id is not None and member.user_id == user_id:
                return True
            if member.agent_id:
                agent = self.agent_repo.get_by_id(member.agent_id)
                if agent and agent.owner_user_id == user_id:
                    return True
        return False

    def can_view_delegation(self, delegation, user, group=None) -> bool:
        if getattr(user, "role", None) == "admin":
            return True

        resolved_group = group or self.group_repo.get_by_id(delegation.group_id)
        if not resolved_group:
            return False

        if self._is_leader_owner(resolved_group, user):
            return True

        if delegation.visibility == "leader_only":
            return False

        return self.is_group_participant(resolved_group.id, user)

    def can_create_delegation(self, group, user) -> bool:
        if getattr(user, "role", None) == "admin":
            return True
        return self._is_leader_owner(group, user)

    async def create_delegation(self, payload: AgentDelegationCreateRequest, user):
        group = self.group_repo.get_by_id(payload.group_id)
        if not group:
            raise AgentDelegationServiceError(status_code=404, detail="Group not found")

        if payload.leader_agent_id != group.leader_agent_id:
            raise AgentDelegationServiceError(status_code=403, detail="leader_agent_id must match group leader")

        leader_agent = self.agent_repo.get_by_id(group.leader_agent_id)
        if not leader_agent:
            raise AgentDelegationServiceError(status_code=404, detail="Group leader agent not found")

        if not self.can_create_delegation(group, user):
            raise AgentDelegationServiceError(status_code=403, detail="Only admin or the group leader owner can create delegations")

        leader_member = self.member_repo.get_by_group_and_agent(group.id, group.leader_agent_id)
        if not leader_member or leader_member.role != "leader":
            raise AgentDelegationServiceError(status_code=403, detail="Leader agent must be a leader member of the group")

        assignee_member = self.member_repo.get_by_group_and_agent(group.id, payload.assignee_agent_id)
        if not assignee_member:
            raise AgentDelegationServiceError(status_code=403, detail="Assignee agent must be a member of the group")

        if payload.visibility not in {"leader_only", "group_visible"}:
            raise AgentDelegationServiceError(status_code=422, detail="Invalid visibility")

        if payload.assignee_agent_id == payload.leader_agent_id:
            raise AgentDelegationServiceError(status_code=409, detail="Leader agent cannot delegate to itself")
        if payload.parent_agent_id and payload.assignee_agent_id == payload.parent_agent_id:
            raise AgentDelegationServiceError(status_code=409, detail="Parent agent cannot delegate to itself")

        assignee_agent = self.agent_repo.get_by_id(payload.assignee_agent_id)
        if not assignee_agent:
            raise AgentDelegationServiceError(status_code=404, detail="Assignee agent not found")
        if assignee_agent.agent_type not in {"specialist", "task"}:
            raise AgentDelegationServiceError(status_code=422, detail="Assignee agent must be a specialist or task agent")
        if payload.parent_agent_id and not self.agent_repo.get_by_id(payload.parent_agent_id):
            raise AgentDelegationServiceError(status_code=404, detail="Parent agent not found")

        input_artifacts = self._parse_json_array(payload.input_artifacts_json, "input_artifacts_json")
        if not all(isinstance(item, dict) for item in input_artifacts):
            raise AgentDelegationServiceError(status_code=422, detail="input_artifacts_json entries must be JSON objects")

        expected_output_schema = self._parse_json_object(
            payload.expected_output_schema_json,
            "expected_output_schema_json",
            default_value={},
        )
        retry_policy = self._parse_json_object(payload.retry_policy_json, "retry_policy_json", default_value={})
        skill_kwargs = self._parse_json_object(payload.skill_kwargs_json, "skill_kwargs_json", default_value={})
        scoped_context_payload = self._parse_optional_json_object(payload.scoped_context_payload_json, "scoped_context_payload_json")

        effective_scoped_context_ref = (payload.scoped_context_ref or "").strip() or None
        if scoped_context_payload is not None and not effective_scoped_context_ref:
            effective_scoped_context_ref = f"ctx-{uuid4()}"

        if effective_scoped_context_ref and scoped_context_payload is None:
            existing_snapshot = self.context_snapshot_repo.get_by_group_and_ref(payload.group_id, effective_scoped_context_ref)
            if not existing_snapshot:
                raise AgentDelegationServiceError(status_code=409, detail="Shared context snapshot not found")

        ephemeral_policy = self._parse_json_object(group.ephemeral_agent_policy_json, "ephemeral_agent_policy_json", default_value={})

        audit_trace = {
            "skill_name": payload.skill_name,
            "skill_kwargs": skill_kwargs,
            "strict_delegation_result": True,
            "ephemeral_agent_policy": ephemeral_policy,
        }
        if skill_kwargs.get("agent_mode") == "task":
            audit_trace["agent_mode"] = "task"
            audit_trace["ephemeral_task_agent_intent"] = True

        delegation = self.delegation_repo.create(
            group_id=payload.group_id,
            parent_agent_id=payload.parent_agent_id,
            leader_agent_id=payload.leader_agent_id,
            assignee_agent_id=payload.assignee_agent_id,
            agent_task_id=None,
            objective=payload.objective,
            leader_session_id=payload.leader_session_id,
            scoped_context_ref=effective_scoped_context_ref,
            input_artifacts_json=json.dumps(input_artifacts),
            expected_output_schema_json=json.dumps(expected_output_schema),
            deadline_at=payload.deadline_at,
            retry_policy_json=json.dumps(retry_policy),
            visibility=payload.visibility,
            status="queued",
            audit_trace_json=json.dumps(audit_trace),
        )

        if scoped_context_payload is not None and effective_scoped_context_ref:
            self.context_snapshot_repo.upsert_by_group_and_ref(
                group_id=payload.group_id,
                context_ref=effective_scoped_context_ref,
                scope_kind="delegation",
                payload_json=json.dumps(scoped_context_payload),
                created_by_user_id=getattr(user, "id", None),
                source_delegation_id=delegation.id,
            )

        task_input_payload = {
            "delegation_id": delegation.id,
            "group_id": payload.group_id,
            "parent_agent_id": payload.parent_agent_id or payload.leader_agent_id,
            "leader_agent_id": payload.leader_agent_id,
            "assignee_agent_id": payload.assignee_agent_id,
            "objective": payload.objective,
            "leader_session_id": payload.leader_session_id,
            "strict_delegation_result": True,
            "scoped_context_ref": effective_scoped_context_ref,
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
            shared_context_ref=effective_scoped_context_ref,
            input_payload_json=json.dumps(task_input_payload),
            status="queued",
            result_payload_json=None,
            retry_count=0,
        )

        delegation.agent_task_id = task.id
        self.delegation_repo.save(delegation)

        dispatch_result = await self.dispatcher.dispatch_task(task.id, self.db)
        if not dispatch_result.dispatched:
            raise AgentDelegationServiceError(status_code=409, detail=f"Delegation task dispatch failed: {dispatch_result.message}")

        updated_delegation = self.delegation_repo.get_by_id(delegation.id)
        return updated_delegation or delegation

    def get_delegation(self, delegation_id: str, user=None):
        delegation = self.delegation_repo.get_by_id(delegation_id)
        if not delegation:
            raise AgentDelegationServiceError(status_code=404, detail="Delegation not found")
        if user is not None and not self.can_view_delegation(delegation, user):
            raise AgentDelegationServiceError(status_code=404, detail="Delegation not found")
        return delegation

    def list_group_delegations(self, group_id: str, user=None, apply_visibility: bool = False):
        group = self.group_repo.get_by_id(group_id)
        if not group:
            raise AgentDelegationServiceError(status_code=404, detail="Group not found")

        delegations = self.delegation_repo.list_by_group_id(group_id)
        if not apply_visibility or user is None:
            return delegations

        return [item for item in delegations if self.can_view_delegation(item, user, group=group)]
