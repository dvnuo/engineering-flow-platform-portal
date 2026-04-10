import json
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.repositories.agent_delegation_repo import AgentDelegationRepository
from app.repositories.agent_coordination_run_repo import AgentCoordinationRunRepository
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.group_shared_context_snapshot_repo import GroupSharedContextSnapshotRepository
from app.schemas.agent_delegation import AgentDelegationCreateRequest, InternalAgentDelegationCreateRequest
from app.services.capability_context_service import CapabilityContextService
from app.services.task_dispatcher import TaskDispatcherService


@dataclass
class AgentDelegationServiceError(Exception):
    status_code: int
    detail: str


@dataclass
class _NormalizedDelegationRequest:
    group_id: str
    parent_agent_id: str | None
    leader_agent_id: str
    assignee_agent_id: str
    objective: str
    leader_session_id: str | None
    scoped_context_ref: str | None
    scoped_context_payload: dict | None
    input_artifacts: list[dict]
    expected_output_schema: dict
    deadline_at: datetime | None
    retry_policy: dict
    visibility: str
    skill_name: str
    skill_kwargs: dict
    coordination_run_id: str | None
    round_index: int


class AgentDelegationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.group_repo = AgentGroupRepository(db)
        self.member_repo = AgentGroupMemberRepository(db)
        self.agent_repo = AgentRepository(db)
        self.task_repo = AgentTaskRepository(db)
        self.delegation_repo = AgentDelegationRepository(db)
        self.context_snapshot_repo = GroupSharedContextSnapshotRepository(db)
        self.run_repo = AgentCoordinationRunRepository(db)
        self.audit_repo = AuditRepository(db)
        self.dispatcher = TaskDispatcherService()
        self.capability_context_service = CapabilityContextService()

    def _assert_skill_allowed_for_agent(self, agent, skill_name: str, error_prefix: str) -> None:
        allowance = self.capability_context_service.get_skill_allowance_detail(self.db, agent, skill_name)
        if allowance.allowed:
            return
        if allowance.reason == "empty_skill_set":
            raise AgentDelegationServiceError(
                status_code=422,
                detail=f"{error_prefix} capability profile has empty skill_set; skill '{allowance.normalized_skill_name}' is not allowed",
            )
        raise AgentDelegationServiceError(
            status_code=422,
            detail=f"{error_prefix} capability profile does not allow skill '{allowance.normalized_skill_name}'",
        )

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

    def _normalize_user_request(self, payload: AgentDelegationCreateRequest) -> _NormalizedDelegationRequest:
        input_artifacts = self._parse_json_array(payload.input_artifacts_json, "input_artifacts_json")
        if not all(isinstance(item, dict) for item in input_artifacts):
            raise AgentDelegationServiceError(status_code=422, detail="input_artifacts_json entries must be JSON objects")

        return _NormalizedDelegationRequest(
            group_id=payload.group_id,
            parent_agent_id=payload.parent_agent_id,
            leader_agent_id=payload.leader_agent_id,
            assignee_agent_id=payload.assignee_agent_id,
            objective=payload.objective,
            leader_session_id=payload.leader_session_id,
            scoped_context_ref=payload.scoped_context_ref,
            scoped_context_payload=self._parse_optional_json_object(payload.scoped_context_payload_json, "scoped_context_payload_json"),
            input_artifacts=input_artifacts,
            expected_output_schema=self._parse_json_object(payload.expected_output_schema_json, "expected_output_schema_json", default_value={}),
            deadline_at=payload.deadline_at,
            retry_policy=self._parse_json_object(payload.retry_policy_json, "retry_policy_json", default_value={}),
            visibility=payload.visibility,
            skill_name=payload.skill_name,
            skill_kwargs=self._parse_json_object(payload.skill_kwargs_json, "skill_kwargs_json", default_value={}),
            coordination_run_id=None,
            round_index=1,
        )

    def _normalize_internal_request(self, payload: InternalAgentDelegationCreateRequest) -> _NormalizedDelegationRequest:
        input_artifacts = payload.input_artifacts or []
        if not isinstance(input_artifacts, list) or not all(isinstance(item, dict) for item in input_artifacts):
            raise AgentDelegationServiceError(status_code=422, detail="input_artifacts must be a list of objects")
        scoped_context_payload = payload.scoped_context_payload
        if scoped_context_payload is not None and not isinstance(scoped_context_payload, dict):
            raise AgentDelegationServiceError(status_code=422, detail="scoped_context_payload must be an object")
        expected_output_schema = payload.expected_output_schema or {}
        if not isinstance(expected_output_schema, dict):
            raise AgentDelegationServiceError(status_code=422, detail="expected_output_schema must be an object")
        retry_policy = payload.retry_policy or {}
        if not isinstance(retry_policy, dict):
            raise AgentDelegationServiceError(status_code=422, detail="retry_policy must be an object")
        skill_kwargs = payload.skill_kwargs or {}
        if not isinstance(skill_kwargs, dict):
            raise AgentDelegationServiceError(status_code=422, detail="skill_kwargs must be an object")

        return _NormalizedDelegationRequest(
            group_id=payload.group_id,
            parent_agent_id=payload.parent_agent_id,
            leader_agent_id=payload.leader_agent_id,
            assignee_agent_id=payload.assignee_agent_id,
            objective=payload.objective,
            leader_session_id=payload.leader_session_id,
            scoped_context_ref=payload.scoped_context_ref,
            scoped_context_payload=scoped_context_payload,
            input_artifacts=input_artifacts,
            expected_output_schema=expected_output_schema,
            deadline_at=payload.deadline_at,
            retry_policy=retry_policy,
            visibility=payload.visibility,
            skill_name=payload.skill_name,
            skill_kwargs=skill_kwargs,
            coordination_run_id=payload.coordination_run_id,
            round_index=payload.round_index or 1,
        )

    async def create_delegation(self, payload: AgentDelegationCreateRequest, user):
        return await self.create_delegation_from_user_request(payload, user)

    async def create_delegation_from_user_request(self, payload: AgentDelegationCreateRequest, user):
        normalized = self._normalize_user_request(payload)
        return await self._create_delegation_core(normalized=normalized, user=user, source="user_api")

    async def create_delegation_from_internal_request(self, payload: InternalAgentDelegationCreateRequest):
        normalized = self._normalize_internal_request(payload)
        return await self._create_delegation_core(normalized=normalized, user=None, source="internal_runtime_api")

    def _assert_delegation_authorized(self, *, group, leader_agent, normalized: _NormalizedDelegationRequest, user, source: str):
        if source == "user_api":
            if not self.can_create_delegation(group, user):
                raise AgentDelegationServiceError(status_code=403, detail="Only admin or the group leader owner can create delegations")
        else:
            if leader_agent.id != normalized.leader_agent_id:
                raise AgentDelegationServiceError(status_code=403, detail="leader_agent_id must match group leader")

    async def _create_delegation_core(self, *, normalized: _NormalizedDelegationRequest, user, source: str):
        group = self.group_repo.get_by_id(normalized.group_id)
        if not group:
            raise AgentDelegationServiceError(status_code=404, detail="Group not found")

        if normalized.leader_agent_id != group.leader_agent_id:
            raise AgentDelegationServiceError(status_code=403, detail="leader_agent_id must match group leader")

        leader_agent = self.agent_repo.get_by_id(group.leader_agent_id)
        if not leader_agent:
            raise AgentDelegationServiceError(status_code=404, detail="Group leader agent not found")

        self._assert_delegation_authorized(group=group, leader_agent=leader_agent, normalized=normalized, user=user, source=source)

        leader_member = self.member_repo.get_by_group_and_agent(group.id, group.leader_agent_id)
        if not leader_member or leader_member.role != "leader":
            raise AgentDelegationServiceError(status_code=403, detail="Leader agent must be a leader member of the group")

        assignee_member = self.member_repo.get_by_group_and_agent(group.id, normalized.assignee_agent_id)
        if not assignee_member:
            raise AgentDelegationServiceError(status_code=403, detail="Assignee agent must be a member of the group")

        if normalized.visibility not in {"leader_only", "group_visible"}:
            raise AgentDelegationServiceError(status_code=422, detail="Invalid visibility")
        if normalized.round_index < 1:
            raise AgentDelegationServiceError(status_code=422, detail="round_index must be >= 1")
        reply_target_type = "leader"
        if reply_target_type != "leader":
            raise AgentDelegationServiceError(status_code=422, detail="Unsupported reply target")

        if normalized.assignee_agent_id == normalized.leader_agent_id:
            raise AgentDelegationServiceError(status_code=409, detail="Leader agent cannot delegate to itself")
        if normalized.parent_agent_id and normalized.assignee_agent_id == normalized.parent_agent_id:
            raise AgentDelegationServiceError(status_code=409, detail="Parent agent cannot delegate to itself")

        assignee_agent = self.agent_repo.get_by_id(normalized.assignee_agent_id)
        if not assignee_agent:
            raise AgentDelegationServiceError(status_code=404, detail="Assignee agent not found")
        if assignee_agent.agent_type not in {"specialist", "task"}:
            raise AgentDelegationServiceError(status_code=422, detail="Assignee agent must be a specialist or task agent")
        self._assert_skill_allowed_for_agent(assignee_agent, normalized.skill_name, "Assignee agent")

        pool_ids: list[str] = []
        has_explicit_pool = bool(group.specialist_agent_pool_json and group.specialist_agent_pool_json.strip())
        if has_explicit_pool:
            try:
                parsed_pool = json.loads(group.specialist_agent_pool_json)
            except json.JSONDecodeError:
                parsed_pool = []
            if isinstance(parsed_pool, list):
                pool_ids = [item for item in parsed_pool if isinstance(item, str)]
        if not has_explicit_pool:
            member_agents = [member.agent_id for member in self.member_repo.list_by_group(group.id) if member.agent_id]
            for agent_id in member_agents:
                if agent_id == group.leader_agent_id:
                    continue
                member_agent = self.agent_repo.get_by_id(agent_id)
                if member_agent and member_agent.agent_type in {"specialist", "task"}:
                    pool_ids.append(agent_id)

        if assignee_agent.id not in pool_ids:
            raise AgentDelegationServiceError(status_code=422, detail="Assignee agent must belong to the specialist agent pool")

        requested_agent_mode = normalized.skill_kwargs.get("agent_mode")
        if requested_agent_mode == "task":
            template_agent_id = normalized.skill_kwargs.get("task_agent_template_id")
            if template_agent_id:
                template_agent = self.agent_repo.get_by_id(template_agent_id)
                if not template_agent:
                    raise AgentDelegationServiceError(status_code=404, detail="task_agent_template_id agent not found")
                self._assert_skill_allowed_for_agent(template_agent, normalized.skill_name, "Template agent")

        if normalized.parent_agent_id and not self.agent_repo.get_by_id(normalized.parent_agent_id):
            raise AgentDelegationServiceError(status_code=404, detail="Parent agent not found")

        effective_scoped_context_ref = (normalized.scoped_context_ref or "").strip() or None
        if normalized.scoped_context_payload is not None and not effective_scoped_context_ref:
            effective_scoped_context_ref = f"ctx-{uuid4()}"

        if effective_scoped_context_ref and normalized.scoped_context_payload is None:
            existing_snapshot = self.context_snapshot_repo.get_by_group_and_ref(normalized.group_id, effective_scoped_context_ref)
            if not existing_snapshot:
                raise AgentDelegationServiceError(status_code=409, detail="Shared context snapshot not found")

        ephemeral_policy = self._parse_json_object(group.ephemeral_agent_policy_json, "ephemeral_agent_policy_json", default_value={})

        audit_trace = {
            "skill_name": normalized.skill_name,
            "skill_kwargs": normalized.skill_kwargs,
            "strict_delegation_result": True,
            "ephemeral_agent_policy": ephemeral_policy,
        }
        if normalized.skill_kwargs.get("agent_mode") == "task":
            audit_trace["agent_mode"] = "task"
            audit_trace["ephemeral_task_agent_intent"] = True

        delegation = self.delegation_repo.create(
            group_id=normalized.group_id,
            parent_agent_id=normalized.parent_agent_id,
            leader_agent_id=normalized.leader_agent_id,
            assignee_agent_id=normalized.assignee_agent_id,
            agent_task_id=None,
            objective=normalized.objective,
            leader_session_id=normalized.leader_session_id,
            origin_session_id=normalized.leader_session_id,
            reply_target_type=reply_target_type,
            coordination_run_id=normalized.coordination_run_id,
            round_index=normalized.round_index,
            scoped_context_ref=effective_scoped_context_ref,
            input_artifacts_json=json.dumps(normalized.input_artifacts),
            expected_output_schema_json=json.dumps(normalized.expected_output_schema),
            deadline_at=normalized.deadline_at,
            retry_policy_json=json.dumps(normalized.retry_policy),
            visibility=normalized.visibility,
            status="queued",
            audit_trace_json=json.dumps(audit_trace),
        )

        if normalized.coordination_run_id:
            existing_run = self.run_repo.get_by_coordination_run_id(normalized.coordination_run_id)
            if existing_run:
                existing_run.group_id = normalized.group_id
                existing_run.leader_agent_id = normalized.leader_agent_id
                existing_run.origin_session_id = normalized.leader_session_id
                existing_run.latest_round_index = max(existing_run.latest_round_index or 1, normalized.round_index)
                existing_run.status = "running"
                existing_run.completed_at = None
                self.run_repo.save(existing_run)
            else:
                self.run_repo.create(
                    group_id=normalized.group_id,
                    leader_agent_id=normalized.leader_agent_id,
                    origin_session_id=normalized.leader_session_id,
                    coordination_run_id=normalized.coordination_run_id,
                    status="running",
                    latest_round_index=normalized.round_index,
                    summary_json=None,
                    completed_at=None,
                )

        if normalized.scoped_context_payload is not None and effective_scoped_context_ref:
            self.context_snapshot_repo.upsert_by_group_and_ref(
                group_id=normalized.group_id,
                context_ref=effective_scoped_context_ref,
                scope_kind="delegation",
                payload_json=json.dumps(normalized.scoped_context_payload),
                created_by_user_id=getattr(user, "id", None),
                source_delegation_id=delegation.id,
            )

        task_input_payload = {
            "delegation_id": delegation.id,
            "group_id": normalized.group_id,
            "parent_agent_id": normalized.parent_agent_id or normalized.leader_agent_id,
            "leader_agent_id": normalized.leader_agent_id,
            "assignee_agent_id": normalized.assignee_agent_id,
            "objective": normalized.objective,
            "leader_session_id": normalized.leader_session_id,
            "origin_session_id": normalized.leader_session_id,
            "reply_target_type": reply_target_type,
            "coordination_run_id": normalized.coordination_run_id,
            "round_index": normalized.round_index,
            "strict_delegation_result": True,
            "agent_mode": "task" if assignee_agent.agent_type == "task" else "specialist",
            "ephemeral_task_agent_id": assignee_agent.id if assignee_agent.agent_type == "task" else None,
            "task_agent_template_id": getattr(assignee_agent, "template_agent_id", None) or normalized.skill_kwargs.get("task_agent_template_id"),
            "task_agent_scope": effective_scoped_context_ref or getattr(assignee_agent, "task_scope_label", None) or normalized.skill_kwargs.get("scope_label"),
            "task_agent_cleanup_policy": getattr(assignee_agent, "task_cleanup_policy", None) or normalized.skill_kwargs.get("cleanup_policy") or ephemeral_policy.get("cleanup_policy"),
            "scoped_context_ref": effective_scoped_context_ref,
            "input_artifacts": normalized.input_artifacts,
            "expected_output_schema": normalized.expected_output_schema,
            "deadline": normalized.deadline_at.isoformat() if normalized.deadline_at else None,
            "retry_policy": normalized.retry_policy,
            "visibility": normalized.visibility,
            "skill_name": normalized.skill_name,
            "skill_kwargs": normalized.skill_kwargs,
        }

        task = self.task_repo.create(
            task_type="delegation_task",
            source="agent",
            group_id=normalized.group_id,
            parent_agent_id=normalized.parent_agent_id or normalized.leader_agent_id,
            assignee_agent_id=normalized.assignee_agent_id,
            owner_user_id=assignee_agent.owner_user_id,
            created_by_user_id=getattr(user, "id", None),
            shared_context_ref=effective_scoped_context_ref,
            input_payload_json=json.dumps(task_input_payload),
            status="queued",
            result_payload_json=None,
            retry_count=0,
        )

        delegation.agent_task_id = task.id
        self.delegation_repo.save(delegation)

        self.audit_repo.create(
            action="create_delegation",
            target_type="agent_delegation",
            target_id=delegation.id,
            user_id=getattr(user, "id", None),
            details={
                "group_id": normalized.group_id,
                "leader_agent_id": normalized.leader_agent_id,
                "assignee_agent_id": normalized.assignee_agent_id,
                "delegation_id": delegation.id,
                "task_id": task.id,
                "visibility": normalized.visibility,
                "scoped_context_ref": effective_scoped_context_ref,
                "input_artifacts_count": len(normalized.input_artifacts),
                "has_expected_output_schema": bool(normalized.expected_output_schema),
                "source": source,
                "coordination_run_id": normalized.coordination_run_id,
                "round_index": normalized.round_index,
            },
        )

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
