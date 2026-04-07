from dataclasses import dataclass
import json

from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories.agent_group_member_repo import AgentGroupMemberRepository
from app.repositories.agent_delegation_repo import AgentDelegationRepository
from app.repositories.agent_group_repo import AgentGroupRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.agent_coordination_run_repo import AgentCoordinationRunRepository
from app.repositories.group_shared_context_snapshot_repo import GroupSharedContextSnapshotRepository
from app.repositories.user_repo import UserRepository
from app.schemas.agent_group import (
    AgentGroupCreateRequest,
    InternalAgentGroupTaskAgentCreateRequest,
    AgentGroupMemberCreateRequest,
    AgentGroupTaskAgentCreateRequest,
    AgentGroupTaskCreateRequest,
    AgentGroupTaskSummaryResponse,
)
from app.services.agent_delegation_service import AgentDelegationService
from app.services.k8s_service import K8sService
from app.utils.naming import runtime_names


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
        self.context_snapshot_repo = GroupSharedContextSnapshotRepository(db)
        self.audit_repo = AuditRepository(db)
        self.run_repo = AgentCoordinationRunRepository(db)
        self.user_repo = UserRepository(db)
        self.k8s_service = K8sService()
        self.settings = get_settings()

    @staticmethod
    def _is_admin(user) -> bool:
        return getattr(user, "role", None) == "admin"

    def _can_read_group_resources(self, group, user) -> bool:
        if user is None:
            return False
        if self._is_admin(user):
            return True
        delegation_service = AgentDelegationService(self.db)
        if delegation_service._is_leader_owner(group, user):
            return True
        return delegation_service.is_group_participant(group.id, user)

    def can_view_group(self, group, user) -> bool:
        return self._can_read_group_resources(group, user)

    def can_manage_group(self, group, user) -> bool:
        if user is None:
            return False
        if self._is_admin(user):
            return True
        delegation_service = AgentDelegationService(self.db)
        return delegation_service._is_leader_owner(group, user)

    def can_manage_group_tasks(self, group, user) -> bool:
        return self.can_manage_group(group, user)

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

    @staticmethod
    def _normalize_pool_ids(agent_ids: list[str] | None) -> list[str]:
        return list(dict.fromkeys([agent_id for agent_id in (agent_ids or []) if agent_id]))

    def _derive_default_specialist_pool(self, leader_agent_id: str, member_agent_ids: list[str]) -> list[str]:
        pool: list[str] = []
        for agent_id in self._normalize_pool_ids(member_agent_ids):
            if agent_id == leader_agent_id:
                continue
            agent = self.agent_repo.get_by_id(agent_id)
            if agent and agent.agent_type in {"specialist", "task"}:
                pool.append(agent_id)
        return list(dict.fromkeys(pool))

    def _validate_specialist_pool_ids(self, *, group, leader_agent_id: str, candidate_agent_ids: list[str], allowed_member_agent_ids: set[str] | None = None) -> list[str]:
        normalized = self._normalize_pool_ids(candidate_agent_ids)
        for agent_id in normalized:
            if agent_id == leader_agent_id:
                raise AgentGroupServiceError(status_code=422, detail="Specialist pool cannot include leader agent")
            if allowed_member_agent_ids is not None and agent_id not in allowed_member_agent_ids:
                raise AgentGroupServiceError(status_code=422, detail="Specialist pool agent must be a group member")
            if group is not None and not self.member_repo.get_by_group_and_agent(group.id, agent_id):
                raise AgentGroupServiceError(status_code=422, detail="Specialist pool agent must be a group member")
            agent = self.agent_repo.get_by_id(agent_id)
            if not agent:
                raise AgentGroupServiceError(status_code=404, detail=f"Agent not found: {agent_id}")
            if agent.agent_type not in {"specialist", "task"}:
                raise AgentGroupServiceError(status_code=422, detail="Specialist pool agents must be specialist or task agents")
        return normalized

    def _get_specialist_pool_ids(self, group) -> list[str]:
        raw = group.specialist_agent_pool_json
        if raw and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                return self._normalize_pool_ids([item for item in parsed if isinstance(item, str)])
        member_agents = [member.agent_id for member in self.member_repo.list_by_group(group.id) if member.agent_id]
        return self._derive_default_specialist_pool(group.leader_agent_id, member_agents)

    def _set_specialist_pool_ids(self, group, specialist_agent_ids: list[str]) -> None:
        group.specialist_agent_pool_json = json.dumps(self._normalize_pool_ids(specialist_agent_ids))
        self.group_repo.save(group)

    @staticmethod
    def _scope_label_from_description(description: str | None) -> str | None:
        if not description:
            return None
        prefix = "ephemeral-task-agent:"
        if not description.startswith(prefix):
            return None
        value = description[len(prefix) :].strip()
        return value or None

    def create_group_with_members(self, payload: AgentGroupCreateRequest, created_by_user_id: int):
        leader_agent = self._get_agent_or_raise(payload.leader_agent_id, "Leader agent not found")
        if leader_agent.agent_type != "workspace":
            raise AgentGroupServiceError(status_code=422, detail="Leader agent must be a workspace agent")

        unique_user_ids = list(dict.fromkeys(payload.member_user_ids))
        unique_agent_ids = list(dict.fromkeys(payload.member_agent_ids))
        allowed_member_agent_ids = set(unique_agent_ids + [payload.leader_agent_id])

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
                specialist_agent_pool_json=None,
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
            explicit_pool = payload.specialist_agent_ids
            if explicit_pool is None:
                final_pool = self._derive_default_specialist_pool(payload.leader_agent_id, unique_agent_ids)
            else:
                final_pool = self._validate_specialist_pool_ids(
                    group=None,
                    leader_agent_id=payload.leader_agent_id,
                    candidate_agent_ids=explicit_pool,
                    allowed_member_agent_ids=allowed_member_agent_ids,
                )
            group.specialist_agent_pool_json = json.dumps(final_pool)
            self.db.commit()
        except AgentGroupServiceError:
            raise
        except Exception as exc:
            self.db.rollback()
            raise AgentGroupServiceError(status_code=400, detail=f"Failed to create group: {exc}") from exc

        self.db.refresh(group)
        members = self.member_repo.list_by_group(group.id)
        self.audit_repo.create(
            action="create_group",
            target_type="agent_group",
            target_id=group.id,
            user_id=created_by_user_id,
            details={
                "group_id": group.id,
                "leader_agent_id": group.leader_agent_id,
                "source": "user_api",
            },
        )
        return group, members

    def add_group_member(self, group_id: str, payload: AgentGroupMemberCreateRequest, user=None):
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
            self.audit_repo.create(
                action="add_group_member",
                target_type="agent_group_member",
                target_id=member.id,
                details={
                    "group_id": group.id,
                    "member_id": member.id,
                    "leader_agent_id": group.leader_agent_id,
                    "source": "user_api",
                },
                user_id=getattr(user, "id", None),
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
        self.audit_repo.create(
            action="add_group_member",
            target_type="agent_group_member",
            target_id=member.id,
            details={
                "group_id": group.id,
                "member_id": member.id,
                "leader_agent_id": group.leader_agent_id,
                "assignee_agent_id": payload.agent_id,
                "source": "user_api",
            },
            user_id=getattr(user, "id", None),
        )
        return member

    def remove_group_member(self, group_id: str, member_id: str, user=None) -> None:
        group = self._get_group_or_raise(group_id)

        member = self.member_repo.get_by_id(member_id)
        if not member or member.group_id != group_id:
            raise AgentGroupServiceError(status_code=404, detail="Group member not found")

        if member.role == "leader" and member.agent_id == group.leader_agent_id:
            raise AgentGroupServiceError(status_code=409, detail="Cannot remove current group leader member")

        member_id = member.id
        self.member_repo.delete(member)
        self.audit_repo.create(
            action="remove_group_member",
            target_type="agent_group_member",
            target_id=member_id,
            details={
                "group_id": group.id,
                "member_id": member_id,
                "leader_agent_id": group.leader_agent_id,
                "source": "user_api",
            },
            user_id=getattr(user, "id", None),
        )

    def list_group_tasks(self, group_id: str, user=None):
        group = self._get_group_or_raise(group_id)
        if user is not None and not self.can_view_group(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to view group tasks")
        return self.task_repo.list_by_group_id(group_id)

    def get_group_task_summary(self, group_id: str, user=None) -> AgentGroupTaskSummaryResponse:
        tasks = self.list_group_tasks(group_id, user=user)
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

    def create_group_task(self, group_id: str, payload: AgentGroupTaskCreateRequest, user=None):
        group = self._get_group_or_raise(group_id)
        if user is not None and not self.can_manage_group_tasks(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to create group tasks")
        assignee_agent = self._get_agent_or_raise(payload.assignee_agent_id, "Assignee agent not found")
        if assignee_agent.agent_type not in {"specialist", "task"}:
            raise AgentGroupServiceError(status_code=422, detail="Assignee agent must be a specialist or task agent")
        assignee_member = self.member_repo.get_by_group_and_agent(group.id, assignee_agent.id)
        if not assignee_member:
            raise AgentGroupServiceError(status_code=403, detail="Assignee agent must be a member of the group")
        pool_ids = self._get_specialist_pool_ids(group)
        if assignee_agent.id not in pool_ids:
            raise AgentGroupServiceError(status_code=422, detail="Assignee agent must belong to the specialist agent pool")

        if payload.parent_agent_id is not None:
            self._get_agent_or_raise(payload.parent_agent_id, "Parent agent not found")

        if payload.shared_context_ref:
            snapshot = self.context_snapshot_repo.get_by_group_and_ref(group.id, payload.shared_context_ref)
            if not snapshot:
                raise AgentGroupServiceError(status_code=404, detail="Shared context snapshot not found")

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

    def get_specialist_pool(self, group_id: str, user) -> list[str]:
        group = self._get_group_or_raise(group_id)
        if not self.can_view_group(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to view group")
        return self._get_specialist_pool_ids(group)

    def get_specialist_pool_descriptors(self, group_id: str, user) -> list[dict]:
        specialist_agent_ids = self.get_specialist_pool(group_id, user)
        descriptors: list[dict] = []
        for agent_id in specialist_agent_ids:
            agent = self.agent_repo.get_by_id(agent_id)
            if not agent:
                continue
            descriptors.append(
                {
                    "agent_id": agent.id,
                    "name": agent.name,
                    "agent_type": agent.agent_type,
                    "status": agent.status,
                    "visibility": agent.visibility,
                }
            )
        return descriptors

    def update_specialist_pool(self, group_id: str, specialist_agent_ids: list[str], user) -> list[str]:
        group = self._get_group_or_raise(group_id)
        if not self.can_manage_group(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to manage group")
        validated = self._validate_specialist_pool_ids(
            group=group,
            leader_agent_id=group.leader_agent_id,
            candidate_agent_ids=specialist_agent_ids,
        )
        self._set_specialist_pool_ids(group, validated)
        self.audit_repo.create(
            action="update_specialist_pool",
            target_type="agent_group",
            target_id=group.id,
            user_id=getattr(user, "id", None),
            details={
                "group_id": group.id,
                "leader_agent_id": group.leader_agent_id,
                "specialist_pool_size": len(validated),
                "source": "user_api",
            },
        )
        return validated

    def create_group_task_agent(
        self,
        group_id: str,
        payload: AgentGroupTaskAgentCreateRequest | InternalAgentGroupTaskAgentCreateRequest,
        user,
        *,
        source: str = "user_api",
    ):
        group = self._get_group_or_raise(group_id)
        if not self.can_manage_group(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to manage group")
        template_agent = self._get_agent_or_raise(payload.template_agent_id, "Template agent not found")
        if template_agent.agent_type not in {"specialist", "task"}:
            raise AgentGroupServiceError(status_code=422, detail="Template agent must be specialist or task")
        if not self.member_repo.get_by_group_and_agent(group.id, template_agent.id):
            raise AgentGroupServiceError(status_code=422, detail="Template agent must be a group member")
        if template_agent.id not in self._get_specialist_pool_ids(group):
            raise AgentGroupServiceError(status_code=422, detail="Template agent must belong to the specialist agent pool")

        visibility = "private"
        if getattr(payload, "visibility", None):
            visibility = payload.visibility

        created = self.agent_repo.create(
            name=payload.name,
            description=f"ephemeral-task-agent:{payload.scope_label or group_id}",
            owner_user_id=getattr(user, "id", None) or template_agent.owner_user_id,
            visibility=visibility,
            status="creating",
            image=template_agent.image,
            repo_url=template_agent.repo_url,
            branch=template_agent.branch,
            cpu=template_agent.cpu,
            memory=template_agent.memory,
            agent_type="task",
            capability_profile_id=template_agent.capability_profile_id,
            policy_profile_id=template_agent.policy_profile_id,
            disk_size_gi=template_agent.disk_size_gi,
            mount_path=template_agent.mount_path,
            namespace=self.settings.agents_namespace,
            deployment_name="",
            service_name="",
            pvc_name="",
            endpoint_path="",
        )
        created.deployment_name, created.service_name, created.pvc_name, created.endpoint_path = runtime_names(created.id)
        self.agent_repo.save(created)
        runtime = self.k8s_service.create_agent_runtime(created)
        created.status = runtime.status
        created.last_error = runtime.message
        self.agent_repo.save(created)

        if not self.member_repo.get_by_group_and_agent(group.id, created.id):
            self.member_repo.create(group_id=group.id, member_type="agent", user_id=None, agent_id=created.id, role="member")
        pool_ids = self._get_specialist_pool_ids(group)
        if created.id not in pool_ids:
            self._set_specialist_pool_ids(group, pool_ids + [created.id])
        self.audit_repo.create(
            action="create_group_task_agent",
            target_type="agent",
            target_id=created.id,
            user_id=getattr(user, "id", None),
            details={
                "group_id": group.id,
                "leader_agent_id": group.leader_agent_id,
                "assignee_agent_id": created.id,
                "source": source,
                "template_agent_id": template_agent.id,
                "scope_label": getattr(payload, "scope_label", None),
                "task_agent_cleanup_policy": getattr(payload, "task_agent_cleanup_policy", None)
                or getattr(payload, "cleanup_policy", None),
                "visibility": visibility,
            },
        )
        return created

    def delete_group_task_agent(
        self,
        group_id: str,
        agent_id: str,
        user,
        *,
        source: str = "user_api",
        cleanup_reason: str | None = None,
    ) -> None:
        group = self._get_group_or_raise(group_id)
        if not self.can_manage_group(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to manage group")
        if agent_id == group.leader_agent_id:
            raise AgentGroupServiceError(status_code=409, detail="Cannot delete leader via task-agent endpoint")
        agent = self._get_agent_or_raise(agent_id, "Agent not found")
        if agent.agent_type != "task":
            raise AgentGroupServiceError(status_code=422, detail="Target agent must be a task agent")
        member = self.member_repo.get_by_group_and_agent(group.id, agent_id)
        if not member:
            raise AgentGroupServiceError(status_code=404, detail="Task agent is not a member of the group")

        pool_ids = [item for item in self._get_specialist_pool_ids(group) if item != agent_id]
        self._set_specialist_pool_ids(group, pool_ids)
        self.member_repo.delete(member)

        runtime = self.k8s_service.delete_agent_runtime(agent, destroy_data=True)
        if runtime.status == "failed":
            raise AgentGroupServiceError(status_code=500, detail=runtime.message or "Delete failed")
        self.agent_repo.delete(agent)
        self.audit_repo.create(
            action="delete_group_task_agent",
            target_type="agent",
            target_id=agent_id,
            user_id=getattr(user, "id", None),
            details={
                "group_id": group.id,
                "leader_agent_id": group.leader_agent_id,
                "assignee_agent_id": agent_id,
                "source": source,
                "destroyed_runtime": True,
                "previous_scope_label": self._scope_label_from_description(agent.description),
                "cleanup_reason": cleanup_reason,
            },
        )

    def auto_cleanup_task_agent(
        self,
        *,
        group_id: str,
        agent_id: str,
        delegation_id: str | None,
        task_id: str | None,
        cleanup_policy: str | None,
        coordination_run_id: str | None = None,
    ) -> bool:
        group = self.group_repo.get_by_id(group_id)
        if not group:
            return False
        agent = self.agent_repo.get_by_id(agent_id)
        if not agent or agent.agent_type != "task":
            return False

        member = self.member_repo.get_by_group_and_agent(group.id, agent_id)
        if member:
            self.member_repo.delete(member)

        pool_ids = [item for item in self._get_specialist_pool_ids(group) if item != agent_id]
        self._set_specialist_pool_ids(group, pool_ids)

        runtime = self.k8s_service.delete_agent_runtime(agent, destroy_data=True)
        if runtime.status == "failed":
            return False
        self.agent_repo.delete(agent)
        self.audit_repo.create(
            action="auto_cleanup_group_task_agent",
            target_type="agent",
            target_id=agent_id,
            details={
                "group_id": group.id,
                "leader_agent_id": group.leader_agent_id,
                "assignee_agent_id": agent_id,
                "delegation_id": delegation_id,
                "task_id": task_id,
                "cleanup_policy": cleanup_policy,
                "coordination_run_id": coordination_run_id,
                "source": "system_cleanup",
                "destroyed_runtime": True,
                "previous_scope_label": self._scope_label_from_description(agent.description),
                "cleanup_reason": cleanup_policy,
            },
        )
        return True

    def list_group_delegations(self, group_id: str, user=None, apply_visibility: bool = False):
        group = self._get_group_or_raise(group_id)
        delegations = self.delegation_repo.list_by_group_id(group_id)
        if not apply_visibility or user is None:
            return delegations

        visibility_service = AgentDelegationService(self.db)
        return [item for item in delegations if visibility_service.can_view_delegation(item, user, group=group)]

    def get_group_task_board(self, group_id: str, user=None, apply_visibility: bool = False) -> dict:
        group = self._get_group_or_raise(group_id)
        delegations = self.list_group_delegations(group_id, user=user, apply_visibility=apply_visibility)
        counts = {
            "queued": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
        }
        for delegation in delegations:
            if delegation.status in counts:
                counts[delegation.status] += 1

        run_map: dict[str, dict] = {}
        for delegation in delegations:
            run_id = (getattr(delegation, "coordination_run_id", None) or "").strip()
            if not run_id:
                continue
            if run_id not in run_map:
                run_map[run_id] = {
                    "coordination_run_id": run_id,
                    "total": 0,
                    "queued": 0,
                    "running": 0,
                    "done": 0,
                    "failed": 0,
                    "latest_round_index": 1,
                }
            bucket = run_map[run_id]
            bucket["total"] += 1
            status = delegation.status
            if status in {"queued", "running", "done", "failed"}:
                bucket[status] += 1
            round_index = getattr(delegation, "round_index", 1) or 1
            if round_index > bucket["latest_round_index"]:
                bucket["latest_round_index"] = round_index

        run_ids = [run_id for run_id in run_map.keys()]
        run_rows = {row.coordination_run_id: row for row in self.run_repo.list_by_group_and_run_ids(group_id, run_ids)}
        runs: list[dict] = []
        for run_id, fallback in run_map.items():
            row = run_rows.get(run_id)
            if row:
                summary = {}
                if row.summary_json:
                    try:
                        parsed = json.loads(row.summary_json)
                    except json.JSONDecodeError:
                        parsed = {}
                    if isinstance(parsed, dict):
                        summary = parsed
                runs.append(
                    {
                        "coordination_run_id": row.coordination_run_id,
                        "total": int(summary.get("total", fallback["total"])),
                        "queued": int(summary.get("queued", fallback["queued"])),
                        "running": int(summary.get("running", fallback["running"])),
                        "done": int(summary.get("done", fallback["done"])),
                        "failed": int(summary.get("failed", fallback["failed"])),
                        "latest_round_index": row.latest_round_index or fallback["latest_round_index"],
                    }
                )
            else:
                runs.append(fallback)

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
            "runs": runs,
        }

    def list_group_shared_context_snapshots(self, group_id: str, user):
        group = self._get_group_or_raise(group_id)
        if not self._can_read_group_resources(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to read group shared contexts")
        return self.context_snapshot_repo.list_by_group_id(group.id)

    def get_group_shared_context_snapshot(self, group_id: str, context_ref: str, user):
        group = self._get_group_or_raise(group_id)
        if not self._can_read_group_resources(group, user):
            raise AgentGroupServiceError(status_code=403, detail="Not allowed to read group shared contexts")
        snapshot = self.context_snapshot_repo.get_by_group_and_ref(group.id, context_ref)
        if not snapshot:
            raise AgentGroupServiceError(status_code=404, detail="Shared context snapshot not found")
        return snapshot
