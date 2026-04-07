import json
from datetime import datetime
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy.orm import Session

from app.repositories.agent_coordination_run_repo import AgentCoordinationRunRepository
from app.repositories.agent_delegation_repo import AgentDelegationRepository
from app.repositories.agent_repo import AgentRepository
from app.repositories.agent_task_repo import AgentTaskRepository
from app.repositories.group_shared_context_snapshot_repo import GroupSharedContextSnapshotRepository
from app.services.capability_context_service import CapabilityContextService
from app.services.proxy_service import ProxyService


@dataclass
class AgentTaskDispatchResult:
    dispatched: bool
    task_id: str
    runtime_status_code: int | None
    task_status: str
    message: str
    result_payload_json: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class TaskDispatcherService:
    def __init__(self) -> None:
        self.proxy_service = ProxyService()
        self.capability_context_service = CapabilityContextService()

    @staticmethod
    def _parse_input_payload(input_payload_json: str | None) -> tuple[dict | None, str | None]:
        if input_payload_json is None or not input_payload_json.strip():
            return None, "input_payload_json must be a valid JSON object"
        try:
            payload = json.loads(input_payload_json)
        except json.JSONDecodeError:
            return None, "input_payload_json must be a valid JSON object"
        if not isinstance(payload, dict):
            return None, "input_payload_json must decode to a JSON object"
        return payload, None

    async def _post_to_runtime(self, url: str, body: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, json=body)

    def _build_failure_payload(self, error_code: str, message: str, status_code: int | None = None) -> str:
        return json.dumps({"ok": False, "error_code": error_code, "message": message, "runtime_status_code": status_code})

    def _build_shared_context_not_found_payload(self, group_id: str | None, context_ref: str) -> str:
        return json.dumps({
            "ok": False,
            "error_code": "shared_context_not_found",
            "message": "Shared context snapshot not found for delegation task",
            "group_id": group_id,
            "shared_context_ref": context_ref,
        })

    @staticmethod
    def _extract_delegation_result(normalized_result_payload_json: str | None) -> tuple[dict | None, str | None]:
        if not normalized_result_payload_json:
            return None, "Missing runtime result payload"
        try:
            payload = json.loads(normalized_result_payload_json)
        except json.JSONDecodeError:
            return None, "Runtime result payload is not valid JSON"
        if not isinstance(payload, dict):
            return None, "Runtime result payload must be a JSON object"

        output_payload = payload.get("output_payload")
        if not isinstance(output_payload, dict):
            return None, "Runtime response missing output_payload object"

        delegation_result = output_payload.get("delegation_result")
        if not isinstance(delegation_result, dict):
            return None, "Runtime response missing delegation_result object"
        return delegation_result, None

    @staticmethod
    def _extract_deleted_task_agent_ids_from_delegation(delegation) -> list[str]:
        audit_raw = getattr(delegation, "audit_trace_json", None)
        if not audit_raw:
            return []
        try:
            parsed = json.loads(audit_raw)
        except Exception:
            return []
        if not isinstance(parsed, dict):
            return []
        cleanup = parsed.get("cleanup")
        if not isinstance(cleanup, dict):
            return []
        ids = cleanup.get("deleted_task_agent_ids")
        if not isinstance(ids, list):
            return []
        return [item for item in ids if isinstance(item, str) and item]

    @staticmethod
    def _append_deleted_task_agent_id_to_delegation(delegation, agent_id: str) -> None:
        if not agent_id:
            return
        try:
            parsed = json.loads(delegation.audit_trace_json) if delegation.audit_trace_json else {}
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        cleanup = parsed.get("cleanup")
        if not isinstance(cleanup, dict):
            cleanup = {}
        existing = cleanup.get("deleted_task_agent_ids")
        if not isinstance(existing, list):
            existing = []
        merged = list(dict.fromkeys([item for item in existing if isinstance(item, str) and item] + [agent_id]))
        cleanup["deleted_task_agent_ids"] = merged
        parsed["cleanup"] = cleanup
        delegation.audit_trace_json = json.dumps(parsed)

    def _sync_delegation_from_task_result(
        self,
        db: Session,
        task,
        normalized_result_payload_json: str | None,
        task_execution_succeeded: bool,
    ) -> None:
        if task.task_type != "delegation_task":
            return

        delegation_repo = AgentDelegationRepository(db)
        delegation = delegation_repo.find_by_agent_task_id(task.id)
        if not delegation:
            return

        delegation_result, error = self._extract_delegation_result(normalized_result_payload_json)
        if error:
            delegation.status = "failed"
            delegation.result_summary = error
            delegation_repo.save(delegation)
            self._sync_coordination_run_from_delegation(db, delegation)
            self._maybe_cleanup_task_agent_after_delegation(db, task, delegation, "failed")
            return

        runtime_status = str(delegation_result.get("status") or "done").lower()
        computed_status = "done" if runtime_status in {"done", "success", "completed"} else "failed"
        if not task_execution_succeeded:
            computed_status = "failed"
        delegation.status = computed_status

        summary = delegation_result.get("summary")
        if summary is None:
            summary = delegation_result.get("result_summary")
        artifacts = delegation_result.get("artifacts")
        if artifacts is None:
            artifacts = delegation_result.get("result_artifacts")
        blockers = delegation_result.get("blockers")
        audit_trace = delegation_result.get("audit_trace")

        delegation.result_summary = summary
        delegation.result_artifacts_json = json.dumps(artifacts) if artifacts is not None else None
        delegation.blockers_json = json.dumps(blockers) if blockers is not None else None
        delegation.next_recommendation = delegation_result.get("next_recommendation")
        delegation.audit_trace_json = json.dumps(audit_trace) if audit_trace is not None else None
        delegation_repo.save(delegation)
        self._sync_coordination_run_from_delegation(db, delegation)
        self._maybe_cleanup_task_agent_after_delegation(db, task, delegation, computed_status)

    def _sync_coordination_run_from_delegation(self, db: Session, delegation) -> None:
        run_id = (getattr(delegation, "coordination_run_id", None) or "").strip()
        if not run_id:
            return
        run_repo = AgentCoordinationRunRepository(db)
        run = run_repo.get_by_coordination_run_id(run_id)
        if not run:
            return

        delegations = AgentDelegationRepository(db).list_by_coordination_run_id(run_id)
        counts = {"queued": 0, "running": 0, "blocked": 0, "done": 0, "failed": 0}
        latest_round_index = 1
        has_blockers = False
        deleted_task_agent_ids: list[str] = []
        for item in delegations:
            status = getattr(item, "status", "")
            if status in counts:
                counts[status] += 1
            latest_round_index = max(latest_round_index, getattr(item, "round_index", 1) or 1)
            blockers_raw = getattr(item, "blockers_json", None)
            if blockers_raw:
                try:
                    parsed_blockers = json.loads(blockers_raw)
                except Exception:
                    parsed_blockers = blockers_raw
                if parsed_blockers:
                    has_blockers = True
            deleted_task_agent_ids.extend(self._extract_deleted_task_agent_ids_from_delegation(item))

        deleted_task_agent_ids = list(dict.fromkeys(deleted_task_agent_ids))

        if counts["running"] > 0 or counts["queued"] > 0:
            run_status = "running"
        elif counts["failed"] > 0:
            run_status = "failed"
        elif has_blockers:
            run_status = "blocked"
        elif counts["done"] > 0 and counts["done"] == len(delegations):
            run_status = "done"
        else:
            run_status = "running"

        run.status = run_status
        run.latest_round_index = latest_round_index
        run.summary_json = json.dumps(
            {
                "total": len(delegations),
                "queued": counts["queued"],
                "running": counts["running"],
                "blocked": counts["blocked"],
                "done": counts["done"],
                "failed": counts["failed"],
                "deleted_task_agent_ids": deleted_task_agent_ids,
            }
        )
        if run_status in {"done", "failed"}:
            run.completed_at = datetime.utcnow()
        else:
            run.completed_at = None
        run_repo.save(run)

    @staticmethod
    def _should_cleanup_task_agent(cleanup_policy: str | None, delegation_status: str) -> bool:
        if cleanup_policy == "delete_on_done":
            return delegation_status == "done"
        if cleanup_policy == "delete_on_terminal":
            return delegation_status in {"done", "failed"}
        return False

    def _maybe_cleanup_task_agent_after_delegation(self, db: Session, task, delegation, delegation_status: str) -> None:
        input_payload, payload_error = self._parse_input_payload(task.input_payload_json)
        if payload_error or not input_payload:
            return

        cleanup_policy = input_payload.get("task_agent_cleanup_policy")
        if not isinstance(cleanup_policy, str) or not cleanup_policy.strip():
            return
        cleanup_policy = cleanup_policy.strip()
        if cleanup_policy == "retain":
            return
        if not self._should_cleanup_task_agent(cleanup_policy, delegation_status):
            return

        assignee_agent_id = input_payload.get("ephemeral_task_agent_id") or task.assignee_agent_id
        if not assignee_agent_id:
            return

        agent = AgentRepository(db).get_by_id(assignee_agent_id)
        if not agent or agent.agent_type != "task":
            return
        group_id = task.group_id or delegation.group_id
        if not group_id:
            return

        from app.services.agent_group_service import AgentGroupService

        group_service = AgentGroupService(db)
        cleaned = group_service.auto_cleanup_task_agent(
            group_id=group_id,
            agent_id=assignee_agent_id,
            delegation_id=delegation.id,
            task_id=task.id,
            cleanup_policy=cleanup_policy,
            coordination_run_id=getattr(delegation, "coordination_run_id", None),
        )
        if cleaned:
            self._append_deleted_task_agent_id_to_delegation(delegation, assignee_agent_id)
            AgentDelegationRepository(db).save(delegation)
            self._sync_coordination_run_from_delegation(db, delegation)

    @staticmethod
    def _normalize_runtime_response(response: httpx.Response) -> tuple[bool, str, str]:
        runtime_status_code = response.status_code
        response_text = response.text or ""

        if not (200 <= runtime_status_code < 300):
            payload = {
                "ok": False,
                "error_code": "runtime_http_error",
                "message": f"Runtime returned non-2xx status: {runtime_status_code}",
                "runtime_status_code": runtime_status_code,
            }
            return False, json.dumps(payload), "Runtime returned non-2xx status"

        try:
            response_json = response.json()
        except Exception:
            response_json = None

        if not isinstance(response_json, dict):
            payload = {
                "ok": False,
                "error_code": "malformed_runtime_response",
                "message": "Runtime returned malformed 2xx response: expected JSON object",
                "runtime_status_code": runtime_status_code,
                "raw_response": response_text,
            }
            return False, json.dumps(payload), "Runtime returned malformed response"

        normalized_payload_json = json.dumps(response_json)
        status_value = response_json.get("status")
        if status_value is None:
            return False, normalized_payload_json, "Runtime returned malformed response"

        normalized_status = str(status_value).lower()
        ok_value = response_json.get("ok")

        if normalized_status == "success" and ok_value is not False:
            return True, normalized_payload_json, "Task dispatched successfully"

        if normalized_status in {"error", "blocked"} or ok_value is False:
            return False, normalized_payload_json, "Runtime execution reported failure"

        return False, normalized_payload_json, "Runtime returned malformed response"

    async def dispatch_task(self, task_id: str, db: Session, user=None) -> AgentTaskDispatchResult:
        _ = user
        task_repo = AgentTaskRepository(db)
        agent_repo = AgentRepository(db)
        delegation_repo = AgentDelegationRepository(db)
        context_repo = GroupSharedContextSnapshotRepository(db)

        task = task_repo.get_by_id(task_id)
        if not task:
            return AgentTaskDispatchResult(False, task_id, None, "not_found", "Task not found", None)

        if task.status != "queued":
            return AgentTaskDispatchResult(False, task.id, None, task.status, "Task is not dispatchable", task.result_payload_json)

        if not task.assignee_agent_id:
            task.status = "failed"
            task.result_payload_json = self._build_failure_payload("missing_assignee", "Task has no assignee_agent_id")
            task_repo.save(task)
            return AgentTaskDispatchResult(False, task.id, None, task.status, "Task has no assignee_agent_id", task.result_payload_json)

        agent = agent_repo.get_by_id(task.assignee_agent_id)
        if not agent:
            task.status = "failed"
            task.result_payload_json = self._build_failure_payload("assignee_not_found", "Assignee agent not found")
            task_repo.save(task)
            return AgentTaskDispatchResult(False, task.id, None, task.status, "Assignee agent not found", task.result_payload_json)

        input_payload, payload_error = self._parse_input_payload(task.input_payload_json)
        if payload_error:
            task.status = "failed"
            task.result_payload_json = self._build_failure_payload("invalid_input_payload", payload_error)
            task_repo.save(task)
            return AgentTaskDispatchResult(False, task.id, None, task.status, payload_error, task.result_payload_json)

        delegation = None
        metadata = {
            "portal_task_id": task.id,
            "portal_task_source": task.source,
            "shared_context_ref": task.shared_context_ref,
        }
        profile_id, resolved_profile = self.capability_context_service.resolve_for_agent(db, agent)
        capability_context = self.capability_context_service.build_runtime_capability_context(profile_id, resolved_profile)
        metadata["capability_profile_id"] = capability_context["capability_profile_id"]
        metadata["policy_profile_id"] = agent.policy_profile_id
        metadata["allowed_capability_ids"] = capability_context["allowed_capability_ids"]
        metadata["allowed_capability_types"] = capability_context["allowed_capability_types"]
        metadata["allowed_external_systems"] = capability_context["allowed_external_systems"]
        metadata["allowed_webhook_triggers"] = capability_context["allowed_webhook_triggers"]
        metadata["allowed_actions"] = capability_context["allowed_actions"]
        metadata["allowed_adapter_actions"] = capability_context["allowed_adapter_actions"]
        workflow_rule_id = input_payload.get("workflow_rule_id")
        if workflow_rule_id:
            metadata["portal_workflow_rule_id"] = workflow_rule_id
        subscription_id = input_payload.get("subscription_id")
        if subscription_id:
            metadata["portal_subscription_id"] = subscription_id
        head_sha = input_payload.get("head_sha")
        if head_sha:
            metadata["portal_head_sha"] = head_sha
        dedupe_hint = task.shared_context_ref
        if dedupe_hint:
            metadata["portal_dedupe_hint"] = dedupe_hint

        materialized_context_ref = None
        if task.task_type == "delegation_task":
            delegation = delegation_repo.find_by_agent_task_id(task.id)
            if delegation:
                metadata["portal_delegation_id"] = delegation.id
                metadata["portal_group_id"] = delegation.group_id
                metadata["portal_leader_agent_id"] = delegation.leader_agent_id
                metadata["portal_assignee_agent_id"] = delegation.assignee_agent_id
                metadata["portal_delegation_reply_target"] = getattr(delegation, "reply_target_type", None) or "leader"
                if getattr(delegation, "coordination_run_id", None):
                    metadata["portal_coordination_run_id"] = delegation.coordination_run_id
                metadata["portal_coordination_round_index"] = getattr(delegation, "round_index", 1) or 1
            else:
                if input_payload.get("coordination_run_id"):
                    metadata["portal_coordination_run_id"] = input_payload.get("coordination_run_id")
                if input_payload.get("round_index") is not None:
                    metadata["portal_coordination_round_index"] = input_payload.get("round_index")
            if input_payload.get("strict_delegation_result") is True:
                metadata["strict_delegation_result"] = True
            if input_payload.get("agent_mode") in {"task", "specialist"}:
                metadata["agent_mode"] = input_payload.get("agent_mode")
            if input_payload.get("ephemeral_task_agent_id"):
                metadata["ephemeral_task_agent_id"] = input_payload.get("ephemeral_task_agent_id")
            if input_payload.get("task_agent_template_id"):
                metadata["task_agent_template_id"] = input_payload.get("task_agent_template_id")
            if input_payload.get("task_agent_scope"):
                metadata["task_agent_scope"] = input_payload.get("task_agent_scope")
            if input_payload.get("task_agent_cleanup_policy"):
                metadata["task_agent_cleanup_policy"] = input_payload.get("task_agent_cleanup_policy")

            if task.shared_context_ref:
                snapshot = context_repo.get_by_group_and_ref(task.group_id or "", task.shared_context_ref) if task.group_id else None
                if not snapshot:
                    task.status = "failed"
                    task.result_payload_json = self._build_shared_context_not_found_payload(task.group_id, task.shared_context_ref)
                    task_repo.save(task)
                    if delegation:
                        delegation.status = "failed"
                        delegation_repo.save(delegation)
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Shared context snapshot not found", task.result_payload_json)
                try:
                    parsed_payload = json.loads(snapshot.payload_json)
                except json.JSONDecodeError:
                    parsed_payload = None
                if not isinstance(parsed_payload, dict):
                    task.status = "failed"
                    task.result_payload_json = self._build_failure_payload("invalid_shared_context_payload", "Persisted shared context payload must be a JSON object")
                    task_repo.save(task)
                    if delegation:
                        delegation.status = "failed"
                        delegation_repo.save(delegation)
                    return AgentTaskDispatchResult(False, task.id, None, task.status, "Invalid shared context payload", task.result_payload_json)
                materialized_context_ref = parsed_payload

        runtime_body = {
            "task_id": task.id,
            "task_type": task.task_type,
            "input_payload": input_payload,
            "source": task.source,
            "shared_context_ref": task.shared_context_ref,
            "context_ref": materialized_context_ref,
            "metadata": metadata,
        }
        leader_session_id = (getattr(delegation, "origin_session_id", None) if delegation else None) or input_payload.get("leader_session_id")
        if isinstance(leader_session_id, str) and leader_session_id.strip():
            runtime_body["session_id"] = leader_session_id.strip()
            metadata["portal_leader_session_id"] = leader_session_id.strip()

        try:
            runtime_url = self.proxy_service.build_agent_base_url(agent).rstrip("/") + "/api/tasks/execute"
        except Exception as exc:
            task.status = "failed"
            task.result_payload_json = self._build_failure_payload("runtime_url_error", str(exc))
            task_repo.save(task)
            if delegation:
                delegation.status = "failed"
                delegation_repo.save(delegation)
            return AgentTaskDispatchResult(False, task.id, None, task.status, f"Runtime URL resolution failed: {exc}", task.result_payload_json)

        task.status = "running"
        task_repo.save(task)

        if delegation:
            delegation.status = "running"
            delegation_repo.save(delegation)

        try:
            response = await self._post_to_runtime(runtime_url, runtime_body)
            runtime_status_code = response.status_code
            execution_succeeded, normalized_result_payload_json, dispatch_message = self._normalize_runtime_response(response)
            task.status = "done" if execution_succeeded else "failed"
            task.result_payload_json = normalized_result_payload_json
            task_repo.save(task)
            self._sync_delegation_from_task_result(db, task, normalized_result_payload_json, execution_succeeded)
            return AgentTaskDispatchResult(
                True,
                task.id,
                runtime_status_code,
                task.status,
                dispatch_message,
                task.result_payload_json,
            )
        except Exception as exc:
            task.status = "failed"
            task.result_payload_json = self._build_failure_payload("runtime_request_error", str(exc))
            task_repo.save(task)
            self._sync_delegation_from_task_result(db, task, task.result_payload_json, False)
            return AgentTaskDispatchResult(True, task.id, None, task.status, f"Runtime dispatch request failed: {exc}", task.result_payload_json)
